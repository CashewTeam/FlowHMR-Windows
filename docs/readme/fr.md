# FlowHMR-Windows

[简体中文](../../README.md) · [繁體中文](zh-TW.md) · [English](en.md) · [日本語](ja.md) · [한국어](ko.md) · [Русский](ru.md) · **Français**

**FlowHMR-Windows** permet de capturer des mouvements à partir d’une vidéo, sur votre propre ordinateur. Importez une vidéo, sélectionnez une ou plusieurs personnes, puis reconstruisez leurs mouvements en 3D pour les prévisualiser en synchronisation avec la vidéo et les exporter. La reconstruction utilise le modèle du projet d’origine [FlowHMR](https://github.com/flowhmr/flowhmr). Ce dépôt indépendant maintient l’édition portable pour Windows, la gestion des modèles et des environnements d’exécution, ainsi que le flux de travail de la sélection des personnes à l’export.

**[Télécharger l’édition portable pour Windows](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR-Windows.zip)** · [Notes de version](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest) · [Article FlowHMR](https://arxiv.org/abs/2610.03691)

## Édition de bureau pour Windows

- **Capturez les mouvements dans une seule fenêtre.** Choisissez les personnes et la plage d’images à traiter, puis visualisez côte à côte la vidéo source et le mouvement 3D. La chronologie synchronise la lecture, les déplacements et les boucles. Vous pouvez écouter le son d’origine et alterner entre l’affichage du maillage et du squelette.
- **Prévisualisez et exportez plusieurs personnes ensemble.** Sélectionnez plusieurs personnes dans la même vidéo. L’aperçu combiné et le FBX préservent leurs positions relatives et leurs instants d’entrée dans le cadre. Rouvrez les anciennes tâches depuis l’historique à vignettes.
- **Ajustez le résultat selon la vidéo.** Réglez le lissage des mouvements, la correction des contacts des pieds et la hauteur du sol. Activez HaMeR pour obtenir des mouvements de doigts plus détaillés. La caméra fixe est le mode par défaut ; choisissez la caméra mobile si la caméra pivote, s’incline ou roule visiblement.
- **Traitez les vidéos en local.** Installez l’environnement d’inférence et les modèles dont vous avez besoin. Les modèles sont chargés au démarrage d’une génération. Une fois configurée, l’application peut fonctionner hors ligne. L’interface est disponible en 简体中文, 繁體中文, English, 日本語, 한국어, Русский et Français et mémorise votre choix.
- **Exportez les données du mouvement.** Exportez en NPZ ou FBX. Le FBX peut contenir le squelette animé seul ou inclure le maillage déformable. Vous pouvez ajouter une T Pose ou une A Pose à l’image **−1** sans déplacer la première image du mouvement.

## Extension Blender : FlowHMR 2 MMR

**[Télécharger FlowHMR 2 MMR v1.3.1](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/releases/latest/download/FlowHMR_2_MMR_v1.3.1.zip)** · Auteurs : **小二, GPT** · Blender 3.6 ou ultérieur. Le flux multipersonne a été vérifié avec les versions 3.6.22 et 4.5.3.

L’extension transfère les mouvements du FBX exporté vers un rig de contrôle cible généré par **MMR**. Pour plusieurs personnes, associez les rigs, prévisualisez les mouvements et enregistrez-les en images clés dans la même scène. Chaque cible reçoit sa propre Action, avec son instant d’entrée et sa position relative.

1. Dans Blender, ouvrez **Édition → Préférences → Modules complémentaires** (Add-ons) et installez le ZIP. Dans Blender 3.6, cliquez sur **Installer**. Dans Blender 4.2 ou ultérieur, choisissez **Installer depuis le disque** dans le menu en haut à droite. Activez **FlowHMR 2 MMR**, puis appuyez sur **N** dans la vue 3D pour ouvrir l’onglet du même nom.
2. Importez le FBX de FlowHMR et préparez le rig de contrôle cible généré par MMR. Pour conserver les numéros d’image négatifs du fichier, réglez **Animation Offset** sur `0` dans les options d’importation FBX.
3. Pour une personne, sélectionnez la source et la cible, puis cliquez sur **建立实时预览** (Créer un aperçu en direct). Vérifiez le mouvement, puis cliquez sur **烘焙到新动作** (Enregistrer dans une nouvelle Action). Pour plusieurs personnes, activez **多人模式** (Mode multipersonne), cliquez sur **识别源骨架** (Détecter les rigs source), choisissez une cible par personne, puis utilisez **预览全部** (Tout prévisualiser) ou **一键烘焙全部** (Tout enregistrer).

Le ZIP contient un guide sur la correspondance des os, les ajustements de pose et le placement des pieds.

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
2. Si vous utilisez un GPU NVIDIA, installez un pilote compatible, puis ouvrez **Environnement** (运行环境) en haut à droite pour télécharger et activer une version CUDA. CUDA 12.8 est recommandé pour la série RTX 50. Pour les AMD RX série 9070, consultez la [prise en charge expérimentale de ROCm](../../docs/amd-rocm.md).
3. Dans **Modèles**, choisissez **Télécharger tous les modèles publics** (下载全部公开模型), ou téléchargez les modèles un par un. Les fichiers sont vérifiés avant d’être placés dans `models/`. SAM 3D Body et VGGT-Omega proviennent de ModelScope. Si l’affinage des doigts est inutile, vous pouvez omettre HaMeR lors du téléchargement individuel. Consultez le tableau ci-dessous pour les modèles officiels qui nécessitent une connexion.
4. Déposez une vidéo dans l’application, sélectionnez les personnes et la plage d’images, puis lancez la génération. Vérifiez le résultat dans l’aperçu, puis exportez-le en FBX ou NPZ depuis le menu.

### Quels modèles officiels télécharger ?

Connectez-vous à la [page de téléchargement officielle de MANO](https://mano.is.tue.mpg.de/download.php). **En plus des modèles publics installés à l’étape précédente, la capture de base nécessite un seul des paquets officiels ci-dessous. Si vous avez déjà `mano_v1_2.zip`, vous n’avez pas besoin du paquet neutre.** Si les deux paquets sont installés, l’application utilise le corps neutre par défaut.

| Utilisation | Nom du téléchargement officiel | Dossier d’extraction manuelle |
| --- | --- | --- |
| Modèle de corps neutre pour la capture de mouvements de base | **Extended SMPL+H model** (AMASS), `smplh.tar.xz` | `models/body_models/smplh/` |
| Corps masculin et féminin pour la capture de base ; contient aussi les fichiers MANO nécessaires à l’affinage des doigts | **Models & Code**, `mano_v1_2.zip` | `models/` |

Après le téléchargement, extrayez l’archive. Dans **Modèles**, cliquez sur **Importer un dossier extrait…** (导入已解压文件夹…) et choisissez le dossier. L’application reconnaît les modèles et les copie aux bons emplacements, en conservant les fichiers sources. La fenêtre Modèles indique les fichiers encore manquants. Pour affiner les doigts, il faut les fichiers MANO de `mano_v1_2.zip` et le modèle public **HaMeR**. Téléchargez HaMeR depuis sa ligne ou avec **Télécharger tous les modèles publics**.

Pour l’arborescence d’installation manuelle, les sources de téléchargement et le rôle des modèles, consultez le [guide des fichiers de modèles](../../models/README.md).

## Aide et crédits du projet

- [Utilisation sous Windows, mise à jour et compilation depuis les sources](../../docs/windows.md) · [Signaler un problème](https://github.com/xiaoerofficial021-collab/FlowHMR-Windows/issues/new)
- [Maintenir les traductions de l’interface](../../docs/localization.md)
- [Site officiel du projet FlowHMR](https://flowhmr.github.io/) · [Documentation du projet d’origine pour l’inférence et l’entraînement](https://github.com/flowhmr/flowhmr#readme) · [Format de citation](https://github.com/flowhmr/flowhmr#citation)

La reconstruction du mouvement repose sur **FlowHMR: Physically Plausible Motion Capture from Video** de Zhanke Wang et ses coauteurs. Pour un usage de recherche, citez l’[article d’origine](https://arxiv.org/abs/2610.03691). Le code source de FlowHMR est sous licence [Apache-2.0](../../LICENSE). Les composants et modèles tiers sont distribués selon leurs propres licences. Voir `THIRD_PARTY_NOTICES.md` dans le paquet de distribution et la [fiche du modèle d’origine](https://huggingface.co/fafsaf1/flowhmr-0.46B).
