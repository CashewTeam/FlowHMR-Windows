# FlowHMR-Windows

[简体中文](../../README.md) · [繁體中文](zh-TW.md) · [English](en.md) · [日本語](ja.md) · **한국어** · [Русский](ru.md) · [Français](fr.md)

**FlowHMR-Windows**는 내 컴퓨터에서 동영상으로 모션을 캡처하는 앱입니다. 동영상을 가져와 한 명 이상의 인물을 선택하면 3D 동작을 재구성해 원본과 동기화된 미리보기로 확인하고 내보낼 수 있습니다. 동작 재구성에는 업스트림 [FlowHMR](https://github.com/flowhmr/flowhmr) 모델을 사용합니다. 이 저장소는 Windows 포터블 버전, 모델과 런타임 관리, 인물 선택부터 내보내기까지의 데스크톱 작업 흐름을 독립적으로 유지합니다.

**[Windows 포터블 버전 다운로드](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [릴리스 노트](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [FlowHMR 논문](https://arxiv.org/abs/2610.03691)

## Windows 데스크톱 버전

- **한 창에서 모션 캡처.** 처리할 인물과 프레임 범위를 선택한 뒤 원본 동영상과 3D 모션을 나란히 확인할 수 있습니다. 재생, 탐색, 반복은 타임라인에서 동기화됩니다. 원본 오디오를 재생하고 스킨 메시와 스켈레톤 표시를 전환할 수 있습니다.
- **여러 인물을 함께 미리 보고 내보내기.** 같은 동영상에서 여러 명을 선택할 수 있습니다. 통합 미리보기와 FBX에는 원본 영상에서의 상대 위치와 각 인물의 등장 시점이 유지됩니다. 이전 작업은 썸네일이 있는 기록에서 다시 열 수 있습니다.
- **영상에 맞춰 모션 조정.** 모션 스무딩, 발 접촉 보정, 바닥 높이를 조절할 수 있습니다. 더 자세한 손가락 모션이 필요하면 HaMeR를 켜세요. 기본값은 고정 카메라입니다. 카메라가 뚜렷하게 방향을 바꾸거나 위아래로 기울거나 회전할 때 이동 카메라를 선택하세요.
- **로컬에서 실행.** 필요한 추론 환경과 모델을 설치하면 생성이 시작될 때만 모델을 불러옵니다. 설정이 끝나면 오프라인에서도 처리할 수 있습니다. 简体中文, 繁體中文, English, 日本語, 한국어, Русский, Français를 지원하며 앱을 다시 열면 마지막으로 선택한 언어를 사용합니다.
- **모션 데이터 내보내기.** NPZ와 FBX를 지원합니다. FBX에 애니메이션 스켈레톤만 담거나 스킨 메시도 포함할 수 있습니다. **−1 프레임**에 T Pose 또는 A Pose를 추가해도 원래 모션의 시작 프레임은 바뀌지 않습니다.

## Blender용 애드온: FlowHMR 2 MMR

**[FlowHMR 2 MMR v1.3.1 다운로드](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR_2_MMR_v1.3.1.zip)** · 제작: **小二, GPT** · Blender 3.6 이상 지원. 여러 인물의 작업 흐름은 3.6.22와 4.5.3에서 검증했습니다.

내보낸 FBX 모션을 **MMR**로 생성한 대상 컨트롤 리그로 옮기는 애드온입니다. 여러 인물도 같은 장면에서 리그를 연결하고, 결과를 미리 본 다음 한 번에 베이크할 수 있습니다. 각 대상에는 별도의 Action이 생성되며 등장 시점과 상대 위치가 유지됩니다.

1. Blender의 **편집 → 환경설정 → 애드온**에서 ZIP을 설치합니다. Blender 3.6은 **설치**를 클릭하고 Blender 4.2 이상은 오른쪽 위 메뉴에서 **디스크에서 설치**를 선택합니다. **FlowHMR 2 MMR**를 활성화한 다음 3D 뷰에서 **N** 키를 눌러 같은 이름의 탭을 엽니다.
2. FlowHMR FBX를 가져오고 MMR로 생성한 대상 컨트롤 리그를 준비합니다. 파일의 음수 프레임 번호를 유지하려면 FBX 가져오기 설정에서 **Animation Offset**을 `0`으로 설정하세요.
3. 한 명인 경우 원본과 대상을 선택하고 **建立实时预览**(실시간 미리보기 만들기)를 클릭합니다. 동작을 확인하고 **烘焙到新动作**(새 액션으로 베이크)를 실행합니다. 여러 명인 경우 **多人模式**(다중 인물 모드)를 켠 뒤 **识别源骨架**(원본 리그 감지)를 클릭합니다. 각 인물의 대상을 선택하고 **预览全部**(모두 미리보기) 또는 **一键烘焙全部**(모두 베이크)를 실행합니다.

애드온 ZIP에는 뼈 매핑, 자세 미세 조정, 발 위치 맞춤 사용법이 들어 있습니다.

## 예제 동영상

썸네일을 클릭하면 MP4가 열립니다. 네 영상 모두 중립 SMPL-H 체형을 30 FPS의 정면 직교 뷰로 표시하며 선택한 범위의 원본 오디오와 동기화되어 있습니다.

| 드리블과 댄스 스텝 | 춤 |
| --- | --- |
| [![드리블과 댄스 스텝](../../assets/demos/01-ikun-thumb.png)](../../assets/demos/01-ikun-front.mp4) | [![춤](../../assets/demos/02-selei-thumb.png)](../../assets/demos/02-selei-front.mp4) |
| **무대 캐릭터의 움직임** | **손동작과 스텝** |
| [![무대 캐릭터의 움직임](../../assets/demos/03-test2-thumb.png)](../../assets/demos/03-test2-front.mp4) | [![손동작과 스텝](../../assets/demos/04-test3-thumb.png)](../../assets/demos/04-test3-front.mp4) |

직접 실행해 보려면 저장소의 [예제 영상](../../assets/example/example.mp4)을 사용하세요.

## 빠른 시작

1. 64비트 Windows 10 또는 11에서 `FlowHMR-Windows.zip` 전체를 압축 해제합니다. 압축을 푼 `FlowHMR` 폴더를 열고 `FlowHMR.exe`를 실행합니다. 기본 패키지에는 Python, 데스크톱 UI 및 FBX 내보내기 구성 요소가 포함됩니다.
2. NVIDIA GPU를 사용하는 경우 호환 드라이버를 설치하고, 오른쪽 위의 **런타임**(运行环境)에서 CUDA 버전을 다운로드해 활성화합니다. RTX 50 시리즈에는 CUDA 12.8을 권장합니다. AMD RX 9070 시리즈는 [ROCm 실험적 지원](../../docs/amd-rocm.md)을 참고하세요.
3. **모델**을 열어 **공개 모델 모두 다운로드**(下载全部公开模型)를 선택하거나 하나씩 다운로드합니다. 공개 모델은 검증 후 `models/`에 배치되며 SAM 3D Body와 VGGT-Omega는 ModelScope에서 받습니다. 손가락 보정이 필요 없다면 개별 다운로드에서 HaMeR를 건너뛸 수 있습니다. 로그인이 필요한 공식 모델은 아래 표를 참고하세요.
4. 앱에 동영상을 끌어 놓고 인물과 프레임 범위를 선택해 생성을 시작합니다. 생성된 모션을 미리 본 뒤 메뉴에서 FBX 또는 NPZ로 내보낼 수 있습니다.

### 어떤 공식 모델을 받아야 하나요?

[MANO 공식 다운로드 페이지](https://mano.is.tue.mpg.de/download.php)에 로그인하세요. **기본 모션 캡처는 앞 단계에서 설치한 공개 모델에 아래 공식 패키지 중 하나만 추가하면 됩니다. `mano_v1_2.zip`이 이미 있다면 중립 체형 패키지를 따로 받지 않아도 됩니다.** 두 패키지를 모두 설치하면 기본적으로 중립 체형이 사용됩니다.

| 용도 | 공식 페이지의 다운로드 항목 | 수동 압축 해제 위치 |
| --- | --- | --- |
| 기본 모션 캡처에 사용할 수 있는 중립 체형 | **Extended SMPL+H model**(AMASS), `smplh.tar.xz` | `models/body_models/smplh/` |
| 기본 모션 캡처에 사용할 수 있는 남성·여성 체형과 손가락 보정에 필요한 MANO 파일 | **Models & Code**, `mano_v1_2.zip` | `models/` |

패키지를 내려받아 압축을 푼 다음 **모델**에서 **압축을 푼 폴더 가져오기…**(导入已解压文件夹…)를 눌러 해당 폴더를 선택합니다. 앱이 모델을 찾아 알맞은 위치에 복사하며 원본 파일은 그대로 유지합니다. 모델 창에서 빠진 파일을 확인할 수 있습니다. 손가락 보정에는 `mano_v1_2.zip`의 MANO 파일과 공개 **HaMeR** 모델이 필요합니다. HaMeR는 해당 행에서 개별 다운로드하거나 **공개 모델 모두 다운로드**로 함께 받을 수 있습니다.

수동 설치의 폴더 구조, 다운로드 출처, 각 모델의 용도는 [모델 파일 안내](../../models/README.md)를 참고하세요.

## 도움말 및 프로젝트 출처

- [Windows 사용, 업그레이드 및 소스 빌드](../../docs/windows.md) · [문제 신고](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/new)
- [UI 번역 유지 관리](../../docs/localization.md)
- [FlowHMR 공식 프로젝트 페이지](https://flowhmr.github.io/) · [업스트림 추론 및 학습 문서](https://github.com/flowhmr/flowhmr#readme) · [업스트림 인용 형식](https://github.com/flowhmr/flowhmr#citation)

모션 재구성은 Zhanke Wang 등의 **FlowHMR: Physically Plausible Motion Capture from Video**를 기반으로 합니다. 연구에 사용하는 경우 [원본 논문](https://arxiv.org/abs/2610.03691)을 인용해 주세요. FlowHMR 소스 코드는 [Apache-2.0](../../LICENSE)을 따릅니다. 타사 구성 요소와 모델은 각자의 라이선스에 따라 배포됩니다. 릴리스 패키지의 `THIRD_PARTY_NOTICES.md`와 [업스트림 모델 카드](https://huggingface.co/fafsaf1/flowhmr-0.46B)를 참고하세요.
