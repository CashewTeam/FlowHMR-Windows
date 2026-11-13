# FlowHMR-Windows

[简体中文](../../README.md) · [繁體中文](zh-TW.md) · [English](en.md) · [日本語](ja.md) · [한국어](ko.md) · [Русский](ru.md) · **Français**

**FlowHMR-Windows** permet de capturer des mouvements à partir d’une vidéo, sur votre propre ordinateur. Importez une vidéo, sélectionnez une ou plusieurs personnes, puis reconstruisez leurs mouvements en 3D pour les prévisualiser en synchronisation avec la vidéo et les exporter. La reconstruction utilise le modèle du projet d’origine [FlowHMR](https://github.com/flowhmr/flowhmr). Ce dépôt indépendant maintient l’édition portable pour Windows, la gestion des modèles et des environnements d’exécution, ainsi que le flux de travail de la sélection des personnes à l’export.

**[Télécharger l’édition portable pour Windows](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [Notes de version](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [Article FlowHMR](https://arxiv.org/abs/2610.03691)

## Nouveautés de la v0.3.5

- **Capture de mouvements sur CPU.** Les processeurs Intel et AMD x64 peuvent traiter les vidéos en mode caméra fixe ou mobile sans GPU. L’affinage des doigts avec HaMeR est indisponible dans ce mode.
- **Nouvelles options GPU expérimentales.** Ajout d’environnements pour les GPU intégrés AMD, les Radeon RX 5000 et Intel XPU. Sur les systèmes AMD compatibles avec GPU intégré, SAM peut répartir le travail entre CPU et GPU pour réduire l’utilisation de la mémoire graphique. Intel XPU utilise le GPU pour encoder les images avec SAM et le CPU pour les autres étapes.
- **Détection des personnes limitée à la plage choisie.** L’application analyse uniquement les images sélectionnées et relance la détection si la plage change. L’aperçu et l’alignement de la scène conservent la numérotation des images de la vidéo source.
- **Import de personnages locaux avec un proxy système.** Correction d’un problème qui permettait à un proxy d’intercepter les requêtes locales pour les fichiers PMX, VRM et FBX ([#7](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/7)).

[Notes de version](../release-windows.md) · [v0.3.4 : correction de l’alignement de scène en cas de mémoire vidéo limitée](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/tag/windows-v0.3.4)

## Configuration requise et matériel compatible

| Composant | Configuration et recommandations |
| --- | --- |
| Système | Windows 10 ou 11 en 64 bits. Windows 11 est recommandé et requis pour AMD ROCm et Intel XPU. |
| RAM | Au moins 16 Go. 32 Go ou plus sont recommandés, en particulier avec le CPU ou un GPU intégré. Les longues vidéos peuvent en demander davantage. |
| Mode CPU seul | Intel / AMD x64. Aucun GPU ni mémoire vidéo requis. 32 Go de mémoire vive ou plus sont recommandés. |
| Mémoire vidéo (GPU dédiés uniquement) | Capture de mouvements vérifiée avec 6 Go de mémoire vidéo. 8 Go ou plus sont recommandés. |

| Matériel | Environnement | Statut |
| --- | --- | --- |
| NVIDIA GeForce RTX 50 | CUDA 12.8 | Vérifié |
| NVIDIA GeForce RTX 30 / 40 | CUDA 12.1 | Vérifié |
| NVIDIA GeForce RTX 20 | CUDA 12.1 | Expérimental |
| NVIDIA GeForce GTX 10 | CUDA 12.1 | Expérimental |
| AMD Radeon RX 9000 / 7000 | [ROCm](../amd-rocm.md) | Vérifié |
| AMD Radeon RX 5000 | [ROCm](../amd-rocm.md) | Expérimental |
| GPU intégrés AMD Radeon (dont les 740M / 760M / 780M et certains modèles 800M) | [ROCm](../amd-rocm.md) | Expérimental (GPU du 9950X vérifié ; caméra fixe recommandée) |
| Intel Arc A / B et certains GPU intégrés Core Ultra Arc | [Intel XPU](../windows.md) | Expérimental (le GPU encode uniquement les images pour SAM) |
| Processeurs Intel / AMD x64 | [CPU seul](../windows.md) | Vérifié (AMD Ryzen 9 9950X) ; Intel expérimental |

L’affinage des doigts avec HaMeR est indisponible en mode CPU et avec Intel XPU. L’estimation des mains intégrée à SAM reste active.

## Édition de bureau pour Windows

- **Capturez les mouvements dans une seule fenêtre.** Choisissez les personnes et la plage d’images à traiter, puis visualisez côte à côte la vidéo source et le mouvement 3D. La chronologie synchronise la lecture, les déplacements et les boucles. Vous pouvez écouter le son d’origine et alterner entre l’affichage du maillage et du squelette.
- **Prévisualisez et exportez plusieurs personnes ensemble.** Sélectionnez plusieurs personnes dans la même vidéo. L’aperçu combiné et le FBX préservent leurs positions relatives et leurs instants d’entrée dans le cadre. Rouvrez les anciennes tâches depuis l’historique à vignettes.
- **Ajustez le résultat selon la vidéo.** Réglez le lissage des mouvements, la correction des contacts des pieds et la hauteur du sol. Sur un environnement GPU compatible, activez HaMeR pour obtenir des mouvements de doigts plus détaillés. La caméra fixe est le mode par défaut ; choisissez la caméra mobile si la caméra pivote, s’incline ou roule visiblement.
- **Traitez les vidéos en local.** Installez l’environnement d’inférence et les modèles dont vous avez besoin. Les modèles sont chargés au démarrage d’une génération. Une fois configurée, l’application peut fonctionner hors ligne. L’interface est disponible en 简体中文, 繁體中文, English, 日本語, 한국어, Русский et Français et mémorise votre choix.
- **Export de mouvements.** Exportez en FBX FlowHMR, Mixamo, MetaHuman ou Maya AdvancedSkeleton, en VMD MMD, en VRM Animation (VRMA) ou en NPZ. Les FBX FlowHMR et Mixamo peuvent inclure un maillage et une pose T ou A à l’image **−1**. Voir le [guide d’export](../retarget-export.md).
- **Personnages personnalisés.** Prévisualisez un PMX, un VRM 0.x ou 1.0, ou un FBX Mixamo ou MetaHuman avec maillage. Choisissez un personnage par personne et conservez les réglages des os. Les rigs PMX compatibles proposent IK et FK.

## Vidéos d’exemple

Cliquez sur une vignette pour ouvrir le MP4. Les quatre vidéos montrent le modèle SMPL-H neutre en vue de face orthographique à 30 images/s. Le son d’origine est synchronisé sur la plage sélectionnée.

| Dribble et pas de danse | Danse |
| --- | --- |
| [![Dribble et pas de danse](../../assets/demos/01-ikun-thumb.png)](../../assets/demos/01-ikun-front.mp4) | [![Danse](../../assets/demos/02-selei-thumb.png)](../../assets/demos/02-selei-front.mp4) |
| **Mouvements d’un personnage de scène** | **Gestes et pas** |
| [![Mouvements d’un personnage de scène](../../assets/demos/03-test2-thumb.png)](../../assets/demos/03-test2-front.mp4) | [![Gestes et pas](../../assets/demos/04-test3-thumb.png)](../../assets/demos/04-test3-front.mp4) |

Pour tester vous-même l’application, utilisez la [vidéo source d’exemple](../../assets/example/example.mp4) du dépôt.

## Démarrage rapide

1. Sous Windows 10 ou 11 en 64 bits, extrayez entièrement `FlowHMR-Windows.zip`, ouvrez le dossier `FlowHMR` extrait et lancez `FlowHMR.exe`. Le paquet de base comprend Python, l’interface de bureau et le composant d’export FBX.
2. Ouvrez **Environnement** en haut à droite, puis téléchargez et activez l’environnement adapté à votre matériel d’après le tableau ci-dessus. Mettez à jour votre pilote graphique si nécessaire. Le mode CPU ne nécessite pas de pilote graphique.
3. Dans **Modèles**, choisissez **Télécharger tous les modèles publics** (下载全部公开模型), ou téléchargez les modèles un par un. Les fichiers sont vérifiés avant d’être placés dans `models/`. SAM 3D Body et VGGT-Omega proviennent de ModelScope. Si l’affinage des doigts est inutile, vous pouvez omettre HaMeR lors du téléchargement individuel. Consultez le tableau ci-dessous pour les modèles officiels qui nécessitent une connexion.
4. Déposez une vidéo dans l’application, sélectionnez les personnes et la plage d’images, puis lancez la génération. Vérifiez le résultat dans l’aperçu, puis exportez-le en FBX, VMD, VRMA ou NPZ depuis le menu.

L’application recherche les mises à jour en arrière-plan. Cliquez sur **Rechercher des mises à jour…** à côté du sélecteur de langue pour lire les notes de version, installer une mise à jour depuis un ZIP téléchargé ou choisir une version précédente. Si GitHub est inaccessible, vous pouvez continuer à utiliser la version actuelle.

### Quels modèles officiels télécharger ?

Connectez-vous à la [page de téléchargement officielle de MANO](https://mano.is.tue.mpg.de/download.php). **En plus des modèles publics installés à l’étape précédente, la capture de base nécessite un seul des paquets officiels ci-dessous. Si vous avez déjà `mano_v1_2.zip`, vous n’avez pas besoin du paquet neutre.** Si les deux paquets sont installés, l’application utilise le corps neutre par défaut.

| Utilisation | Nom du téléchargement officiel | Dossier d’extraction manuelle |
| --- | --- | --- |
| Modèle de corps neutre pour la capture de mouvements de base | **Extended SMPL+H model** (AMASS), `smplh.tar.xz` | `models/body_models/smplh/` |
| Corps masculin et féminin pour la capture de base ; contient aussi les fichiers MANO nécessaires à l’affinage des doigts | **Models & Code**, `mano_v1_2.zip` | `models/` |

Après le téléchargement, choisissez **Modèles → Importer une archive de modèles…** (模型 → 导入模型压缩包…) et sélectionnez directement `mano_v1_2.zip` ou `smplh.tar.xz`. L’application extrait l’archive, identifie les modèles et les installe aux bons emplacements. Elle conserve l’archive d’origine et indique quels groupes sont prêts et quels fichiers manquent. Si vous avez déjà extrait l’archive, choisissez **Importer un dossier extrait…** (导入已解压文件夹…).

Pour affiner les doigts, il faut les fichiers MANO de `mano_v1_2.zip` et le modèle public **HaMeR**. Téléchargez HaMeR depuis sa ligne ou avec **Télécharger tous les modèles publics**.

Pour l’arborescence d’installation manuelle, les sources de téléchargement et le rôle des modèles, consultez le [guide des fichiers de modèles](../../models/README.md).

## Aide et crédits du projet

- [Utilisation sous Windows, mise à jour et compilation depuis les sources](../../docs/windows.md) · [Signaler un problème](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/new)
- [Maintenir les traductions de l’interface](../../docs/localization.md)
- [Site officiel du projet FlowHMR](https://flowhmr.github.io/) · [Documentation du projet d’origine pour l’inférence et l’entraînement](https://github.com/flowhmr/flowhmr#readme) · [Format de citation](https://github.com/flowhmr/flowhmr#citation)

La reconstruction du mouvement repose sur **FlowHMR: Physically Plausible Motion Capture from Video** de Zhanke Wang et ses coauteurs. Pour un usage de recherche, citez l’[article d’origine](https://arxiv.org/abs/2610.03691). Le code source de FlowHMR est sous licence [Apache-2.0](../../LICENSE). Les composants et modèles tiers sont distribués selon leurs propres licences. Voir `THIRD_PARTY_NOTICES.md` dans le paquet de distribution et la [fiche du modèle d’origine](https://huggingface.co/fafsaf1/flowhmr-0.46B).
