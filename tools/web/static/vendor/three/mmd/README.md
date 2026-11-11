# Three.js r160 MMD preview sources

These files are the MMD subset of Three.js `r160`, vendored to load PMX models without a CDN. Three.js and its examples use the MIT license in [../LICENSE](../LICENSE).

- [MMDLoader.js](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/loaders/MMDLoader.js) (original SHA-256 `21aa7619ab6234db28c334f83ae3e5cf6aa0771d4fcf8add3b638ba28f308ced`)
- [TGALoader.js](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/loaders/TGALoader.js) (original SHA-256 `ce938459ed99d5234f9665270cca8552464d0d1703a094e49760631e6b14c7a3`)
- [MMDToonShader.js](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/shaders/MMDToonShader.js) (original SHA-256 `b1e01ab0442c3c04ec7fbfb024c15d7636c1ff94de260a839ab4ecbd69ffca5b`)
- [mmdparser.module.js](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/libs/mmdparser.module.js) (original SHA-256 `77562d8a91949633814a6ce1f4060dcb95b1a672e8549f41e6524eb6f1c30c68`), based on [Takahiro's MMD parser](https://github.com/takahirox/mmd-parser), with its [MIT license](MMD-PARSER-LICENSE).

Local adjustments: the parser uses the PMX header's UTF-8 or UTF-16LE encoding, compares version 2.1 at Float32 precision, and reads QDEF weights and the byte layouts of additional UV, flip and impulse morphs. Three.js previews SDEF and QDEF using linear skinning; the FlowHMR adapter counts affected vertices. The preview does not simulate PMX morphs or physics. The texture loaders reject truncated TGA data and correct the triangle stride in the alpha check.
