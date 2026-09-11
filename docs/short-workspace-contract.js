(function (root, factory) {
  const contract = factory();
  if (typeof module === "object" && module.exports) module.exports = contract;
  if (root) root.ShortWorkspaceContract = contract;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const TIME_BASIS = "source";
  const EPSILON = 1e-9;
  const TRANSITION_TYPES = new Set(["crossfade", "fade", "cut"]);

  function finiteNumber(value, fallback = 0) {
    const result = Number(value);
    return Number.isFinite(result) ? result : fallback;
  }

  function clamp(value, minimum, maximum) {
    return Math.min(maximum, Math.max(minimum, value));
  }

  function normalizeTransition(transition) {
    const rawType = String(transition && transition.type || "crossfade").toLowerCase();
    const type = TRANSITION_TYPES.has(rawType) ? rawType : "crossfade";
    const duration = Math.max(0, finiteNumber(transition && transition.duration, 0.5));
    return { type, duration };
  }

  function normalizeClip(clip, index) {
    const timeBasis = String(clip && clip.time_basis || "").toLowerCase();
    if (timeBasis !== TIME_BASIS) {
      throw new Error("short clip time_basis must be source; convert output ranges at the bridge");
    }
    const sourceStart = Math.max(0, finiteNumber(clip && clip.startSec, 0));
    const sourceEnd = Math.max(sourceStart, finiteNumber(clip && clip.endSec, sourceStart));
    return {
      clipId: clip && clip.id !== undefined ? clip.id : index,
      sourceStart,
      sourceEnd,
      duration: sourceEnd - sourceStart,
      time_basis: timeBasis,
    };
  }

  function buildTimeline(clips, transition) {
    const normalizedTransition = normalizeTransition(transition);
    const timelineClips = [];
    let totalDuration = 0;

    (Array.isArray(clips) ? clips : []).forEach((clip, index) => {
      const normalized = normalizeClip(clip, index);
      let overlap = 0;
      if (
        index > 0
        && normalizedTransition.type !== "cut"
        && normalizedTransition.duration > 0
      ) {
        overlap = Math.min(normalizedTransition.duration, totalDuration, normalized.duration);
      }
      const outputStart = Math.max(0, totalDuration - overlap);
      const outputEnd = outputStart + normalized.duration;
      timelineClips.push({
        ...normalized,
        overlap,
        outputStart,
        outputEnd,
        contribution: normalized.duration - overlap,
      });
      totalDuration = outputEnd;
    });

    return {
      time_basis: TIME_BASIS,
      transition: normalizedTransition,
      clips: timelineClips,
      totalDuration,
    };
  }

  function outputToSource(timeline, outputTime) {
    if (!timeline || !Array.isArray(timeline.clips) || timeline.clips.length === 0) return null;
    const clampedOutput = clamp(finiteNumber(outputTime, 0), 0, timeline.totalDuration);
    let active = timeline.clips[0];

    // During an overlap both clips are visible. The mock has one source player,
    // so the incoming (later) clip is the deterministic preview owner.
    for (const entry of timeline.clips) {
      if (clampedOutput + EPSILON < entry.outputStart) break;
      active = entry;
    }

    const localTime = clamp(clampedOutput - active.outputStart, 0, active.duration);
    return {
      time_basis: TIME_BASIS,
      clipId: active.clipId,
      outputTime: clampedOutput,
      sourceTime: active.sourceStart + localTime,
    };
  }

  function sourceToOutput(timeline, clipId, sourceTime) {
    if (!timeline || !Array.isArray(timeline.clips)) return null;
    const entry = timeline.clips.find((candidate) => String(candidate.clipId) === String(clipId));
    if (!entry) return null;
    const clampedSource = clamp(
      finiteNumber(sourceTime, entry.sourceStart),
      entry.sourceStart,
      entry.sourceEnd,
    );
    return {
      time_basis: TIME_BASIS,
      clipId: entry.clipId,
      outputTime: entry.outputStart + clampedSource - entry.sourceStart,
      sourceTime: clampedSource,
    };
  }

  function normalizeCuts(cuts, sourceDuration) {
    const duration = Math.max(0, finiteNumber(sourceDuration, 0));
    const ordered = (Array.isArray(cuts) ? cuts : [])
      .map((cut) => ({
        start: clamp(finiteNumber(cut && (cut.sourceStart ?? cut.start), 0), 0, duration),
        end: clamp(finiteNumber(cut && (cut.sourceEnd ?? cut.end), 0), 0, duration),
      }))
      .filter((cut) => cut.end > cut.start)
      .sort((left, right) => left.start - right.start || left.end - right.end);
    const merged = [];
    for (const cut of ordered) {
      const previous = merged[merged.length - 1];
      if (!previous || cut.start > previous.end + EPSILON) {
        merged.push({ ...cut });
      } else {
        previous.end = Math.max(previous.end, cut.end);
      }
    }
    return merged;
  }

  function buildNormalKeepRanges(sourceDuration, cuts) {
    const duration = Math.max(0, finiteNumber(sourceDuration, 0));
    const normalizedCuts = normalizeCuts(cuts, duration);
    const keepRanges = [];
    let sourceCursor = 0;
    let outputCursor = 0;

    for (const cut of normalizedCuts) {
      if (cut.start > sourceCursor + EPSILON) {
        const rangeDuration = cut.start - sourceCursor;
        keepRanges.push({
          sourceStart: sourceCursor,
          sourceEnd: cut.start,
          outputStart: outputCursor,
          outputEnd: outputCursor + rangeDuration,
        });
        outputCursor += rangeDuration;
      }
      sourceCursor = Math.max(sourceCursor, cut.end);
    }
    if (sourceCursor < duration - EPSILON) {
      keepRanges.push({
        sourceStart: sourceCursor,
        sourceEnd: duration,
        outputStart: outputCursor,
        outputEnd: outputCursor + duration - sourceCursor,
      });
    }
    return keepRanges;
  }

  function bridgeSelectionToSourceRanges(selection, sourceDuration, cuts) {
    const basis = String(selection && selection.time_basis || "").toLowerCase();
    if (basis !== "source" && basis !== "output") {
      throw new Error("selection.time_basis must be 'source' or 'output'");
    }
    const duration = Math.max(0, finiteNumber(sourceDuration, 0));
    const rawStart = finiteNumber(selection && selection.start, 0);
    const rawEnd = finiteNumber(selection && selection.end, rawStart);
    const start = Math.min(rawStart, rawEnd);
    const end = Math.max(rawStart, rawEnd);

    if (basis === "source") {
      const sourceStart = clamp(start, 0, duration);
      const sourceEnd = clamp(end, 0, duration);
      return sourceEnd > sourceStart
        ? [{ time_basis: TIME_BASIS, sourceStart, sourceEnd }]
        : [];
    }

    const keepRanges = buildNormalKeepRanges(duration, cuts);
    const outputDuration = keepRanges.length > 0 ? keepRanges[keepRanges.length - 1].outputEnd : 0;
    const outputStart = clamp(start, 0, outputDuration);
    const outputEnd = clamp(end, 0, outputDuration);
    const mapped = [];
    for (const range of keepRanges) {
      const intersectionStart = Math.max(outputStart, range.outputStart);
      const intersectionEnd = Math.min(outputEnd, range.outputEnd);
      if (intersectionEnd <= intersectionStart + EPSILON) continue;
      mapped.push({
        time_basis: TIME_BASIS,
        sourceStart: range.sourceStart + intersectionStart - range.outputStart,
        sourceEnd: range.sourceStart + intersectionEnd - range.outputStart,
      });
    }
    return mapped;
  }

  function buildExportPlan(clips, transition) {
    const timeline = buildTimeline(clips, transition);
    return {
      time_basis: TIME_BASIS,
      durationSeconds: timeline.totalDuration,
      transition: timeline.transition,
      clips: timeline.clips.map((entry) => ({
        id: entry.clipId,
        start: entry.sourceStart,
        end: entry.sourceEnd,
      })),
    };
  }

  return {
    TIME_BASIS,
    buildExportPlan,
    buildNormalKeepRanges,
    buildTimeline,
    bridgeSelectionToSourceRanges,
    normalizeTransition,
    outputToSource,
    sourceToOutput,
  };
});
