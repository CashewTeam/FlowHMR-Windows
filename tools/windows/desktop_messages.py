"""Messages shown by the Windows launcher before the WebView app is ready."""

from pathlib import Path
from tempfile import NamedTemporaryFile


MESSAGES = {
    "zh-CN": {
        "starting": ("正在启动 FlowHMR", "正在打开界面。推理模型将在首次生成时加载。"),
        "switching": ("正在切换运行环境", "推理服务重启后会自动回到 FlowHMR。"),
        "failed": ("FlowHMR 启动失败", "请查看 logs/desktop.log 和 logs/desktop-launcher.log。"),
        "desktop_runtime_failed": ("无法加载 FlowHMR 窗口运行环境", "请完整解压 Windows 便携包后重试。详细原因见 logs/desktop.log。"),
        "server_busy": ("已有 FlowHMR 服务在运行", "请先完成旧版正在进行的任务并退出。若服务仍在运行，请在安装目录执行 stop-web.cmd，再打开 FlowHMR。"),
    },
    "zh-TW": {
        "starting": ("正在啟動 FlowHMR", "正在開啟介面。推論模型會在首次產生時載入。"),
        "switching": ("正在切換執行環境", "推論服務重新啟動後會自動回到 FlowHMR。"),
        "failed": ("FlowHMR 啟動失敗", "請查看 logs/desktop.log 和 logs/desktop-launcher.log。"),
        "desktop_runtime_failed": ("無法載入 FlowHMR 視窗執行環境", "請完整解壓縮 Windows 可攜版後再試。詳細原因見 logs/desktop.log。"),
        "server_busy": ("已有 FlowHMR 服務正在執行", "請先完成舊版正在執行的工作並結束程式。若服務仍在執行，請在安裝目錄執行 stop-web.cmd，再開啟 FlowHMR。"),
    },
    "en": {
        "starting": ("Starting FlowHMR", "Opening the workspace. Models load when you first generate motion."),
        "switching": ("Switching runtime", "FlowHMR will reopen when the service restarts."),
        "failed": ("FlowHMR could not start", "See logs/desktop.log and logs/desktop-launcher.log."),
        "desktop_runtime_failed": ("Could not load the FlowHMR desktop runtime", "Extract the complete Windows portable package and try again. See logs/desktop.log for details."),
        "server_busy": ("Another FlowHMR service is running", "Finish any tasks in the previous version and close it. If the service is still running, run stop-web.cmd in the installation folder, then reopen FlowHMR."),
    },
    "ja": {
        "starting": ("FlowHMR を起動中", "画面を開いています。モデルは最初にモーションを生成するときに読み込まれます。"),
        "switching": ("実行環境を切り替え中", "サービスの再起動後に FlowHMR が自動で開きます。"),
        "failed": ("FlowHMR を起動できませんでした", "logs/desktop.log と logs/desktop-launcher.log を確認してください。"),
        "desktop_runtime_failed": ("FlowHMR のウィンドウを読み込めません", "Windows ポータブル版をすべて展開して再試行してください。詳細は logs/desktop.log を確認してください。"),
        "server_busy": ("別の FlowHMR サービスが実行中です", "旧版の処理が完了したら終了してください。サービスが残っている場合は、インストール先の stop-web.cmd を実行してから FlowHMR を開き直してください。"),
    },
    "ko": {
        "starting": ("FlowHMR 시작 중", "화면을 여는 중입니다. 모델은 처음 모션을 생성할 때 불러옵니다."),
        "switching": ("실행 환경 전환 중", "서비스를 다시 시작하면 FlowHMR이 자동으로 열립니다."),
        "failed": ("FlowHMR을 시작할 수 없습니다", "logs/desktop.log와 logs/desktop-launcher.log를 확인하세요."),
        "desktop_runtime_failed": ("FlowHMR 창 실행 환경을 불러올 수 없습니다", "Windows 포터블 패키지 전체를 압축 해제한 뒤 다시 시도하세요. 자세한 내용은 logs/desktop.log를 확인하세요."),
        "server_busy": ("다른 FlowHMR 서비스가 실행 중입니다", "이전 버전의 작업을 완료한 뒤 종료하세요. 서비스가 계속 실행되면 설치 폴더의 stop-web.cmd를 실행한 후 FlowHMR을 다시 여세요."),
    },
    "ru": {
        "starting": ("Запуск FlowHMR", "Открывается рабочая область. Модели загрузятся при первом создании движения."),
        "switching": ("Смена среды выполнения", "FlowHMR откроется после перезапуска службы."),
        "failed": ("Не удалось запустить FlowHMR", "См. logs/desktop.log и logs/desktop-launcher.log."),
        "desktop_runtime_failed": ("Не удалось загрузить среду рабочего окна FlowHMR", "Распакуйте весь переносимый пакет Windows и повторите попытку. Подробности в logs/desktop.log."),
        "server_busy": ("Другая служба FlowHMR уже работает", "Дождитесь завершения задач в предыдущей версии и закройте её. Если служба всё ещё работает, запустите stop-web.cmd из папки установки и снова откройте FlowHMR."),
    },
    "fr": {
        "starting": ("Démarrage de FlowHMR", "Ouverture de l’interface. Les modèles seront chargés à la première génération."),
        "switching": ("Changement d’environnement", "FlowHMR se rouvrira après le redémarrage du service."),
        "failed": ("FlowHMR n’a pas pu démarrer", "Consultez logs/desktop.log et logs/desktop-launcher.log."),
        "desktop_runtime_failed": ("Impossible de charger l’environnement de bureau FlowHMR", "Décompressez le paquet portable Windows dans son intégralité et réessayez. Consultez logs/desktop.log pour les détails."),
        "server_busy": ("Un autre service FlowHMR est en cours d’exécution", "Terminez les tâches de la version précédente et fermez-la. Si le service est encore actif, exécutez stop-web.cmd dans le dossier d’installation, puis rouvrez FlowHMR."),
    },
}
_PREFERENCE = Path(__file__).resolve().parents[2] / ".cache/desktop-language.txt"
_ARCHIVE_FILTER = {
    "zh-CN": "模型压缩包", "zh-TW": "模型壓縮檔", "en": "Model archives",
    "ja": "モデルのアーカイブ", "ko": "모델 압축 파일", "ru": "Архивы моделей", "fr": "Archives de modèles",
}


def read_language():
    try:
        saved = _PREFERENCE.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        saved = ""
    return saved if saved in MESSAGES else "zh-CN"


def model_archive_filter():
    return f"{_ARCHIVE_FILTER[read_language()]} (*.zip;*.tar.xz;*.tar.gz;*.tar;*.tgz)"


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
