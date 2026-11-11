# Three.js r160 FBX character sources

These sources are vendored to load local Mixamo and MetaHuman FBX characters without a CDN. Three.js and its examples use the [Three.js MIT license](../LICENSE).

| File | Pinned upstream r160 file | Original SHA-256 |
| --- | --- | --- |
| `FBXLoader.js` | [Loader](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/loaders/FBXLoader.js) | `e8c2a47dffc04ca5699839a6d80fa9140fd9c943f62cea8d074736d7de9c5d98` |
| `NURBSCurve.js` | [Curve](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/curves/NURBSCurve.js) | `34dee297704432358680934a8d5acae35f05c71aa59ce7a8e97decb0ac38e452` |
| `NURBSUtils.js` | [Curve utilities](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/curves/NURBSUtils.js) | `86bcf4002721854739f6dc7fd185b5eb0540d6b8676feaae6979bb605baac85e` |
| `fflate.module.js` | [Bundled fflate 0.6.9](https://github.com/mrdoob/three.js/blob/r160/examples/jsm/libs/fflate.module.js) | `5686c3d02432a47d8e31f16cabc2d725bbf4531a3abb37cc6fd00142256e3730` |

See [FFLATE-LICENSE](FFLATE-LICENSE) for the bundled fflate's MIT license.

Local FBXLoader adjustments expose axis/unit metadata, allow a byte/file-only texture provider without invoking the URL loader, skip source cameras/lights/animation in character mode, and share bones across skins linked to the same FBX node while retaining each skin's bind matrices. Original skin weight counts are reported: Three.js r160 uses the four strongest influences for preview. The FlowHMR adapter also reuses the [r160 TGA loader](../mmd/README.md) already included for PMX.
