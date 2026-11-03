# Third-party code and models
| What | License | Where used | Notes |
|---|---|---|---|
| Marqo/nsfw-image-detection-384 | Apache-2.0 | crop classifier | exported to ONNX by `models/export_marqo.py` |
| NudeNet 320n, 640m (v3.4 weights) | MIT (PyPI nudenet 3.4.2) | detectors | ONNX from the GitHub release |
| AdamCodd/vit-base-nsfw-detector | Apache-2.0 | server second opinion | upstream ONNX int8 |
| taufiqdp/mobilenetv4_conv_small.e2400_r224_in1k_nsfw_classifier | Apache-2.0 | Android sorter (`crop_gate`) | upstream ONNX, pinned revision; base MobileNetV4 weights from timm (Apache-2.0) |
| ONNX Runtime | MIT | all platforms | |
| ONNX Runtime (DirectML package) | MIT | Windows helper | |
| WiX Toolset 5 | MS-RL | Windows installer, CI only | |
| Geist (font) | SIL OFL 1.1, Vercel | server portal | woff2 subsets in `server/app/portal/static/fonts` |
| Tailscale container image | BSD-3 | compose sidecar | |
| ONNX Runtime Web | MIT | browser extension | WASM files copied into the extension at build |
| esbuild, vitest, pngjs, Playwright | MIT / Apache-2.0 | extension build and tests | dev only |
| ONNX Runtime Android | MIT | Android app | |
| Shizuku API and provider (dev.rikka.shizuku 13.1.5) | Apache-2.0 | Android app, optional finger tracking | the Shizuku app itself is installed by the owner |
| OkHttp, Gson, AndroidX security-crypto, Kotlin coroutines | Apache-2.0 | Android app | |
| ONNX Runtime iOS (onnxruntime-swift-package-manager) | MIT | iOS broadcast extension | |
| XcodeGen | MIT | iOS project generation, CI only | |

Add a row whenever code or a model is borrowed or adapted.
