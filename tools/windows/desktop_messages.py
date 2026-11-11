"""Messages shown by the Windows launcher before the WebView app is ready."""

from pathlib import Path
from tempfile import NamedTemporaryFile


MESSAGES = {
    "zh-CN": {
        "starting": ("正在启动 FlowHMR", "正在打开界面。推理模型将在首次生成时加载。"),
        "switching": ("正在切换运行环境", "推理服务重启后会自动回到 FlowHMR。"),
        "failed": ("FlowHMR 启动失败", "请查看 logs/desktop.log 和 logs/desktop-launcher.log。"),
    },
    "zh-TW": {
        "starting": ("正在啟動 FlowHMR", "正在開啟介面。推論模型會在首次產生時載入。"),
        "switching": ("正在切換執行環境", "推論服務重新啟動後會自動回到 FlowHMR。"),
        "failed": ("FlowHMR 啟動失敗", "請查看 logs/desktop.log 和 logs/desktop-launcher.log。"),
    },
    "en": {
        "starting": ("Starting FlowHMR", "Opening the workspace. Models load when you first generate motion."),
        "switching": ("Switching runtime", "FlowHMR will reopen when the service restarts."),
        "failed": ("FlowHMR could not start", "See logs/desktop.log and logs/desktop-launcher.log."),
    },
    "ja": {
        "starting": ("FlowHMR を起動中", "画面を開いています。モデルは最初にモーションを生成するときに読み込まれます。"),
        "switching": ("実行環境を切り替え中", "サービスの再起動後に FlowHMR が自動で開きます。"),
        "failed": ("FlowHMR を起動できませんでした", "logs/desktop.log と logs/desktop-launcher.log を確認してください。"),
    },
    "ko": {
        "starting": ("FlowHMR 시작 중", "화면을 여는 중입니다. 모델은 처음 모션을 생성할 때 불러옵니다."),
        "switching": ("실행 환경 전환 중", "서비스를 다시 시작하면 FlowHMR이 자동으로 열립니다."),
        "failed": ("FlowHMR을 시작할 수 없습니다", "logs/desktop.log와 logs/desktop-launcher.log를 확인하세요."),
    },
    "ru": {
        "starting": ("Запуск FlowHMR", "Открывается рабочая область. Модели загрузятся при первом создании движения."),
        "switching": ("Смена среды выполнения", "FlowHMR откроется после перезапуска службы."),
        "failed": ("Не удалось запустить FlowHMR", "См. logs/desktop.log и logs/desktop-launcher.log."),
    },
    "fr": {
        "starting": ("Démarrage de FlowHMR", "Ouverture de l’interface. Les modèles seront chargés à la première génération."),
        "switching": ("Changement d’environnement", "FlowHMR se rouvrira après le redémarrage du service."),
        "failed": ("FlowHMR n’a pas pu démarrer", "Consultez logs/desktop.log et logs/desktop-launcher.log."),
    },
}
_PREFERENCE = Path(__file__).resolve().parents[2] / ".cache/desktop-language.txt"


def read_language():
    try:
        saved = _PREFERENCE.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        saved = ""
    return saved if saved in MESSAGES else "zh-CN"


def save_language(language):
    if not isinstance(language, str) or language not in MESSAGES:
        return False
    if language == read_language():
        return True
    _PREFERENCE.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=_PREFERENCE.parent, delete=False) as temporary:
        path = Path(temporary.name)
        try:
            temporary.write(language.encode("ascii"))
            temporary.close()
            path.replace(_PREFERENCE)
        finally:
            temporary.close()
            path.unlink(missing_ok=True)
    return True
