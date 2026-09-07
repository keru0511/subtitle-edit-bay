# Windows runtime contract

Windows の配布版は `runtime/runtime-contract.json` を契約の起点とし、CPU と NVIDIA CUDA 12.8 の
2 プロファイルを提供します。Python は CPython 3.10、FFmpeg/ffprobe は major 6 以上 10 未満です。
`setup.bat` と修復ショートカットは同じ契約を使います。

## 再現と修復

`setup.ps1` は GPU を検出してプロファイルを選び、対応する lock を `--require-hashes` と
`--only-binary=:all:` で新しい `.venv.staging` にインストールします。全パッケージの版、主要 module の
実 import、FFmpeg/ffprobe、CUDA を検証してから既存 `.venv` と入れ替えます。途中で失敗した場合、既存の
実行環境は維持されます。

成功時は `.local/runtime-manifest.json` に、アプリ版、プロファイル、Python、全パッケージ、PyTorch/CUDA、
GPU、FFmpeg/ffprobe、使用した lock と SHA-256 を記録します。GUI の診断情報もこの manifest を表示します。

## lock の更新

直接依存を `runtime/requirements-windows.in` で更新し、同じ uv バージョンを使って Windows x64 / Python 3.10
向け lock を生成します（現在の生成器は `uv 0.11.33` です）。

```powershell
uv pip compile runtime/requirements-windows.in --python-version 3.10 --python-platform x86_64-pc-windows-msvc --generate-hashes --no-emit-index-url --output-file runtime/requirements-windows-cpu.lock
uv pip compile runtime/requirements-windows.in --python-version 3.10 --python-platform x86_64-pc-windows-msvc --generate-hashes --no-emit-index-url --default-index https://pypi.org/simple --index https://download.pytorch.org/whl/cu128 --index-strategy unsafe-best-match --output-file runtime/requirements-windows-cu128.lock
python scripts/runtime_contract.py validate
```

lock と契約はリリース対象 commit に含まれ、installer manifest にそれぞれの SHA-256 が保存されます。
したがってタグを checkout すれば、公開時と同じ入力から runtime を再構築できます。lock の更新は通常の
コード変更と同様にレビューし、Windows の release preparation で CPU runtime を空の venv へ再構築して
検証します。
