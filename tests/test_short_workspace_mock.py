from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from src.short_video_schema import ShortVideo, ShortVideoClip, ShortVideoTransition
from src.short_video_timeline import build_short_video_timeline


REPO_ROOT = Path(__file__).resolve().parents[1]
MOCK_PATH = REPO_ROOT / "docs" / "ui-redesign-mockup.html"


class ShortWorkspaceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if cls.node is None:
            raise unittest.SkipTest("Node.js is required for the HTML mock contract tests")

    def _run_node(self, source: str) -> object:
        result = subprocess.run(
            [self.node, "-e", source],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_cut_boundary_maps_output_time_to_next_source_clip(self) -> None:
        payload = self._run_node(
            """
            const contract = require('./docs/short-workspace-contract.js');
            const clips = [
              {id: 1, startSec: 0.8, endSec: 3.2, time_basis: 'source'},
              {id: 2, startSec: 21.5, endSec: 25.0, time_basis: 'source'},
            ];
            const timeline = contract.buildTimeline(clips, {type: 'cut', duration: 0});
            const boundary = timeline.clips[1].outputStart;
            process.stdout.write(JSON.stringify({
              duration: timeline.totalDuration,
              before: contract.outputToSource(timeline, boundary - 0.1),
              boundary: contract.outputToSource(timeline, boundary),
            }));
            """
        )

        self.assertAlmostEqual(payload["duration"], 5.9)
        self.assertEqual(payload["before"]["clipId"], 1)
        self.assertAlmostEqual(payload["before"]["sourceTime"], 3.1)
        self.assertEqual(payload["boundary"]["clipId"], 2)
        self.assertAlmostEqual(payload["boundary"]["sourceTime"], 21.5)
        self.assertEqual(payload["boundary"]["time_basis"], "source")

    def test_crossfade_duration_mapping_and_export_share_one_timeline(self) -> None:
        payload = self._run_node(
            """
            const contract = require('./docs/short-workspace-contract.js');
            const clips = [
              {id: 1, startSec: 0.8, endSec: 3.2, time_basis: 'source'},
              {id: 2, startSec: 21.5, endSec: 25.0, time_basis: 'source'},
            ];
            const transition = {type: 'crossfade', duration: 0.5};
            const timeline = contract.buildTimeline(clips, transition);
            const exportPlan = contract.buildExportPlan(clips, transition);
            process.stdout.write(JSON.stringify({
              duration: timeline.totalDuration,
              overlap: timeline.clips[1].overlap,
              incoming: contract.outputToSource(timeline, timeline.clips[1].outputStart),
              inverse: contract.sourceToOutput(timeline, 2, 22.0),
              exportDuration: exportPlan.durationSeconds,
              exportBasis: exportPlan.time_basis,
            }));
            """
        )

        self.assertAlmostEqual(payload["duration"], 5.4)
        self.assertAlmostEqual(payload["overlap"], 0.5)
        self.assertEqual(payload["incoming"]["clipId"], 2)
        self.assertAlmostEqual(payload["incoming"]["sourceTime"], 21.5)
        self.assertAlmostEqual(payload["inverse"]["outputTime"], 2.4)
        self.assertAlmostEqual(payload["exportDuration"], payload["duration"])
        self.assertEqual(payload["exportBasis"], "source")

    def test_mock_duration_matches_product_export_timeline(self) -> None:
        clips = [
            ShortVideoClip(segment_id="a", start=0.8, end=3.2),
            ShortVideoClip(segment_id="b", start=21.5, end=25.0),
            ShortVideoClip(segment_id="c", start=27.0, end=27.2),
        ]

        for transition_type, transition_duration in (
            ("cut", 0.0),
            ("crossfade", 0.5),
            ("fade", 10.0),
        ):
            with self.subTest(transition_type=transition_type):
                product = build_short_video_timeline(
                    ShortVideo(
                        clips=clips,
                        transition=ShortVideoTransition(
                            type=transition_type,
                            duration=transition_duration,
                        ),
                    )
                )
                mock = self._run_node(
                    f"""
                    const contract = require('./docs/short-workspace-contract.js');
                    const timeline = contract.buildTimeline(
                      [
                        {{id: 'a', startSec: 0.8, endSec: 3.2, time_basis: 'source'}},
                        {{id: 'b', startSec: 21.5, endSec: 25.0, time_basis: 'source'}},
                        {{id: 'c', startSec: 27.0, endSec: 27.2, time_basis: 'source'}},
                      ],
                      {{type: {json.dumps(transition_type)}, duration: {transition_duration}}}
                    );
                    process.stdout.write(JSON.stringify(timeline));
                    """
                )

                self.assertAlmostEqual(mock["totalDuration"], product.total_duration)
                self.assertEqual(len(mock["clips"]), len(product.clips))
                for mock_clip, product_clip in zip(mock["clips"], product.clips, strict=True):
                    self.assertAlmostEqual(mock_clip["outputStart"], product_clip.output_start)
                    self.assertAlmostEqual(mock_clip["overlap"], product_clip.overlap)

    def test_bridge_splits_output_range_and_rejects_implicit_output_clips(self) -> None:
        payload = self._run_node(
            """
            const contract = require('./docs/short-workspace-contract.js');
            const cuts = [{start: 4, end: 8.5}, {start: 17, end: 21}];
            const mapped = contract.bridgeSelectionToSourceRanges(
              {start: 3, end: 5, time_basis: 'output'}, 30, cuts
            );
            const direct = contract.bridgeSelectionToSourceRanges(
              {start: 3, end: 5, time_basis: 'source'}, 30, cuts
            );
            let rejected = false;
            try {
              contract.buildTimeline(
                [{id: 9, startSec: 0, endSec: 1, time_basis: 'output'}],
                {type: 'cut', duration: 0}
              );
            } catch (error) {
              rejected = /convert output ranges at the bridge/.test(String(error.message));
            }
            let missingBasisRejected = false;
            try {
              contract.buildTimeline(
                [{id: 10, startSec: 0, endSec: 1}],
                {type: 'cut', duration: 0}
              );
            } catch (error) {
              missingBasisRejected = /time_basis must be source/.test(String(error.message));
            }
            process.stdout.write(JSON.stringify({mapped, direct, rejected, missingBasisRejected}));
            """
        )

        self.assertEqual(
            payload["mapped"],
            [
                {"time_basis": "source", "sourceStart": 3, "sourceEnd": 4},
                {"time_basis": "source", "sourceStart": 8.5, "sourceEnd": 9.5},
            ],
        )
        self.assertEqual(
            payload["direct"],
            [{"time_basis": "source", "sourceStart": 3, "sourceEnd": 5}],
        )
        self.assertTrue(payload["rejected"])
        self.assertTrue(payload["missingBasisRejected"])

    def test_mock_seek_playback_tick_and_click_use_the_same_mapping(self) -> None:
        payload = self._run_node(
            """
            const fs = require('fs');
            const vm = require('vm');
            const contract = require('./docs/short-workspace-contract.js');
            const html = fs.readFileSync('./docs/ui-redesign-mockup.html', 'utf8');
            const scripts = [...html.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/gi)]
              .map(match => match[1]).filter(source => source.trim());
            let intervalCallback = null;
            const track = {getBoundingClientRect: () => ({left: 0, width: 100})};
            const context = {
              ShortWorkspaceContract: contract,
              console,
              document: {
                getElementById: id => id === 'short-timeline-track' ? track : null,
                createElement: () => ({classList: {add() {}, remove() {}}, style: {}}),
              },
              window: {addEventListener() {}},
              setInterval: callback => { intervalCallback = callback; return 1; },
              clearInterval: () => { intervalCallback = null; },
              setTimeout: () => 1,
              clearTimeout() {},
            };
            vm.createContext(context);
            vm.runInContext(scripts[scripts.length - 1], context);
            vm.runInContext(`
              currentWorkspace = 'short-artifact';
              shortTransition = {type: 'cut', duration: 0};
              setShortPlayheadOutputTime(2.3);
              seekRelative(0.1);
            `, context);
            const seek = JSON.parse(vm.runInContext('JSON.stringify(shortPlayerState)', context));

            vm.runInContext('setShortPlayheadOutputTime(2.3); togglePlay();', context);
            intervalCallback();
            const playback = JSON.parse(vm.runInContext('JSON.stringify(shortPlayerState)', context));

            const boundaryPercent = (2.4 / 5.9) * 100;
            vm.runInContext(`handleShortTimelineClick({clientX: ${boundaryPercent}})`, context);
            const click = JSON.parse(vm.runInContext('JSON.stringify(shortPlayerState)', context));
            const normalSourceTime = vm.runInContext('currentTimeSeconds', context);
            process.stdout.write(JSON.stringify({seek, playback, click, normalSourceTime}));
            """
        )

        for action in ("seek", "playback", "click"):
            with self.subTest(action=action):
                self.assertEqual(payload[action]["activeClipId"], 2)
                self.assertAlmostEqual(payload[action]["outputTimeSeconds"], 2.4)
                self.assertAlmostEqual(payload[action]["sourceTimeSeconds"], 21.5)
        self.assertAlmostEqual(payload["normalSourceTime"], 4.25)

    def test_mock_meter_playback_end_and_export_share_effective_duration(self) -> None:
        payload = self._run_node(
            """
            const fs = require('fs');
            const vm = require('vm');
            const contract = require('./docs/short-workspace-contract.js');
            const html = fs.readFileSync('./docs/ui-redesign-mockup.html', 'utf8');
            const scripts = [...html.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/gi)]
              .map(match => match[1]).filter(source => source.trim());
            const elements = new Map();
            function element(id = '') {
              return {
                id,
                className: '',
                classList: {add() {}, remove() {}, toggle() {}},
                style: {},
                innerHTML: '',
                innerText: '',
                appendChild() {},
                getBoundingClientRect: () => ({left: 0, width: 100}),
              };
            }
            const context = {
              ShortWorkspaceContract: contract,
              console,
              document: {
                getElementById: id => {
                  if (!elements.has(id)) elements.set(id, element(id));
                  return elements.get(id);
                },
                createElement: () => element(),
              },
              window: {addEventListener() {}},
              setInterval: () => 1,
              clearInterval() {},
              setTimeout: () => 1,
              clearTimeout() {},
            };
            vm.createContext(context);
            vm.runInContext(scripts[scripts.length - 1], context);
            vm.runInContext(`
              currentWorkspace = 'short-artifact';
              renderShortTimeline();
              setShortPlayheadOutputTime(5.35);
              advanceShortPlayback(0.1);
            `, context);
            const player = JSON.parse(vm.runInContext('JSON.stringify(shortPlayerState)', context));
            const exportPlan = JSON.parse(vm.runInContext('JSON.stringify(buildShortExportPlan())', context));
            process.stdout.write(JSON.stringify({
              meter: elements.get('short-total-duration-text').innerText,
              player,
              exportPlan,
            }));
            """
        )

        self.assertEqual(payload["meter"], "5.4秒")
        self.assertAlmostEqual(payload["player"]["outputTimeSeconds"], 0.05)
        self.assertAlmostEqual(payload["exportPlan"]["durationSeconds"], 5.4)
        self.assertEqual(payload["exportPlan"]["time_basis"], "source")

    def test_mock_can_add_source_range_without_subtitles(self) -> None:
        payload = self._run_node(
            """
            const fs = require('fs');
            const vm = require('vm');
            const contract = require('./docs/short-workspace-contract.js');
            const html = fs.readFileSync('./docs/ui-redesign-mockup.html', 'utf8');
            const scripts = [...html.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/gi)]
              .map(match => match[1]).filter(source => source.trim());
            const fields = {
              'clip-add-mode': {value: 'range'},
              'clip-start-sec': {value: '5.5'},
              'clip-end-sec': {value: '7.25'},
            };
            const context = {
              ShortWorkspaceContract: contract,
              console,
              document: {
                getElementById: id => fields[id] || null,
                createElement: () => ({classList: {add() {}, remove() {}}, style: {}}),
              },
              window: {addEventListener() {}},
              setInterval: () => 1,
              clearInterval() {},
              setTimeout: () => 1,
              clearTimeout() {},
              Date,
            };
            vm.createContext(context);
            vm.runInContext(scripts[scripts.length - 1], context);
            vm.runInContext('subtitles = []; shortClips = []; addShortClipFromInput();', context);
            process.stdout.write(vm.runInContext('JSON.stringify(shortClips)', context));
            """
        )

        self.assertEqual(len(payload), 1)
        self.assertAlmostEqual(payload[0]["startSec"], 5.5)
        self.assertAlmostEqual(payload[0]["endSec"], 7.25)
        self.assertEqual(payload[0]["time_basis"], "source")

    def test_short_keyboard_delete_does_not_mutate_normal_timeline(self) -> None:
        payload = self._run_node(
            """
            const fs = require('fs');
            const vm = require('vm');
            const contract = require('./docs/short-workspace-contract.js');
            const html = fs.readFileSync('./docs/ui-redesign-mockup.html', 'utf8');
            const scripts = [...html.matchAll(/<script(?:\\s[^>]*)?>([\\s\\S]*?)<\\/script>/gi)]
              .map(match => match[1]).filter(source => source.trim());
            let keydown = null;
            const context = {
              ShortWorkspaceContract: contract,
              console,
              document: {
                getElementById: () => null,
                createElement: () => ({classList: {add() {}, remove() {}}, style: {}}),
              },
              window: {
                addEventListener: (name, callback) => {
                  if (name === 'keydown') keydown = callback;
                },
              },
              setInterval: () => 1,
              clearInterval() {},
              setTimeout: () => 1,
              clearTimeout() {},
            };
            vm.createContext(context);
            vm.runInContext(scripts[scripts.length - 1], context);
            vm.runInContext("currentWorkspace = 'short-artifact'; initKeyboardShortcuts();", context);
            keydown({
              code: 'Delete',
              key: 'Delete',
              ctrlKey: false,
              metaKey: false,
              shiftKey: false,
              target: {tagName: '', isContentEditable: false},
              preventDefault() {},
            });
            process.stdout.write(vm.runInContext(
              'JSON.stringify({shortClipIds: shortClips.map(clip => clip.id), cuts})',
              context
            ));
            """
        )

        self.assertEqual(payload["shortClipIds"], [2])
        self.assertEqual(
            payload["cuts"],
            [
                {"id": 1, "start": 4, "end": 8.5, "reason": "無音区間"},
                {"id": 2, "start": 17, "end": 21, "reason": "手動指定"},
            ],
        )

    def test_mock_marks_short_as_an_independent_source_time_workspace(self) -> None:
        html = MOCK_PATH.read_text(encoding="utf-8")

        self.assertIn('data-workspace-kind="short-artifact"', html)
        self.assertIn('data-time-basis="source"', html)
        self.assertIn('onclick="openShortWorkspace()"', html)
        self.assertIn('onclick="closeShortWorkspace()"', html)
        self.assertIn('src="./short-workspace-contract.js"', html)
        self.assertNotIn('onclick="navigateView(\'short\')"', html)


if __name__ == "__main__":
    unittest.main()
