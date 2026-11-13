# FlowHMR-Windows

[简体中文](../../README.md) · [繁體中文](zh-TW.md) · [English](en.md) · [日本語](ja.md) · [한국어](ko.md) · [Русский](ru.md) · **Français**

**FlowHMR-Windows** permet de capturer des mouvements à partir d’une vidéo, sur votre propre ordinateur. Importez une vidéo, sélectionnez une ou plusieurs personnes, puis reconstruisez leurs mouvements en 3D pour les prévisualiser en synchronisation avec la vidéo et les exporter. La reconstruction utilise le modèle du projet d’origine [FlowHMR](https://github.com/flowhmr/flowhmr). Ce dépôt indépendant maintient l’édition portable pour Windows, la gestion des modèles et des environnements d’exécution, ainsi que le flux de travail de la sélection des personnes à l’export.

**[Télécharger l’édition portable pour Windows](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [Notes de version](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [Article FlowHMR](https://arxiv.org/abs/2610.03691)

## Nouveautés de la v0.3.3

- **Mises à jour.** Redémarrez la v0.3.2 pour vérifier en arrière-plan si la v0.3.3 est disponible, ou utilisez le bouton de vérification en haut à droite. Après téléchargement et vérification, la mise à jour s’installe au prochain démarrage. Vos modèles, personnages, environnements, tâches et réglages sont conservés.
- **Lots SAM adaptatifs.** L’application mesure la mémoire utilisée pendant l’inférence et ajuste chaque lot en fonction de la mémoire vidéo et de la RAM disponibles. En cas d’échec d’allocation, elle réessaie avec un lot plus petit et indique la cause si une seule image ne peut pas être traitée. L’échantillonnage des mains par SAM se règle sur 1, 2 ou 4 images et peut être utilisé avec l’affinage HaMeR.
- **Service MCP local.** Démarrez-le depuis la barre d’outils. Un client compatible peut créer, consulter ou annuler des tâches, contrôler la lecture et le squelette des personnages, et exporter les mouvements. Correction des exports MCP après sélection automatique d’une seule personne et de l’actualisation de la progression dans l’interface Web distante. [Guide MCP](../mcp.md) · [Notes de version complètes](../release-windows.md)

## Édition de bureau pour Windows

- **Capturez les mouvements dans une seule fenêtre.** Choisissez les personnes et la plage d’images à traiter, puis visualisez côte à côte la vidéo source et le mouvement 3D. La chronologie synchronise la lecture, les déplacements et les boucles. Vous pouvez écouter le son d’origine et alterner entre l’affichage du maillage et du squelette.
- **Prévisualisez et exportez plusieurs personnes ensemble.** Sélectionnez plusieurs personnes dans la même vidéo. L’aperçu combiné et le FBX préservent leurs positions relatives et leurs instants d’entrée dans le cadre. Rouvrez les anciennes tâches depuis l’historique à vignettes.
- **Ajustez le résultat selon la vidéo.** Réglez le lissage des mouvements, la correction des contacts des pieds et la hauteur du sol. Activez HaMeR pour obtenir des mouvements de doigts plus détaillés. La caméra fixe est le mode par défaut ; choisissez la caméra mobile si la caméra pivote, s’incline ou roule visiblement.
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
2. Installez un pilote compatible, puis ouvrez **Environnement** (运行环境) en haut à droite pour télécharger et activer l’environnement adapté à votre GPU. Pour NVIDIA, choisissez CUDA ; la version 12.8 est recommandée pour la série RTX 50. Si votre AMD Radeon RX série 9000 utilise `gfx1201`, suivez les instructions ci-dessous pour essayer ROCm.
3. Dans **Modèles**, choisissez **Télécharger tous les modèles publics** (下载全部公开模型), ou téléchargez les modèles un par un. Les fichiers sont vérifiés avant d’être placés dans `models/`. SAM 3D Body et VGGT-Omega proviennent de ModelScope. Si l’affinage des doigts est inutile, vous pouvez omettre HaMeR lors du téléchargement individuel. Consultez le tableau ci-dessous pour les modèles officiels qui nécessitent une connexion.
4. Déposez une vidéo dans l’application, sélectionnez les personnes et la plage d’images, puis lancez la génération. Vérifiez le résultat dans l’aperçu, puis exportez-le en FBX, VMD, VRMA ou NPZ depuis le menu.

### AMD Radeon RX série 9000 : prise en charge expérimentale

AMD et NVIDIA utilisent les mêmes fichiers `FlowHMR-Windows.zip` et `FlowHMR.exe`. Sous Windows 11 avec une Radeon RX série 9000 à architecture `gfx1201`, ouvrez **Environnement** au premier lancement, puis téléchargez et activez **ROCm 7.14.0 · gfx1201**. L’application mémorise ce choix et sélectionne automatiquement un GPU AMD compatible. L’installation des modèles se fait de la même façon.

Le téléchargement et l’extraction des paquets officiels AMD ainsi que le chargement des bibliothèques ont été vérifiés. La capture de mouvement complète, y compris l’estimation du mouvement de la caméra et HaMeR, reste à valider sur une RX 9070 XT. Consultez le [guide d’installation et de validation AMD](../../docs/amd-rocm.md).

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
