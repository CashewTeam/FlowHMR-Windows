"""Maya timing setup for 30 FPS FlowHMR AdvancedSkeleton FBX."""

import maya.cmds as cmds
import maya.mel as mel
from maya.api.OpenMaya import MTime


SOURCE_FPS = 30.0
SOURCE_UNIT = "ntsc"
RANGE_FLAGS = ("animationStartTime", "animationEndTime", "minTime", "maxTime")
PLAYBACK_FLAGS = ("playbackSpeed", "by")
WINDOW = "flowhmrAdvTiming"


def _scene_fps():
    return MTime(1.0, MTime.kSeconds).asUnits(MTime.uiUnit())


def _playback_options(flags):
    return {flag: cmds.playbackOptions(query=True, **{flag: True}) for flag in flags}


def _timeline_seconds():
    fps = _scene_fps()
    ranges = {flag: frame / fps for flag, frame in _playback_options(RANGE_FLAGS).items()}
    return ranges, cmds.currentTime(query=True) / fps


def _set_timeline(unit, timeline):
    if cmds.currentUnit(query=True, time=True) != unit:
        cmds.currentUnit(time=unit, updateAnimation=True)
    # Reapply the time slider range explicitly because unit changes can round it.
    fps = _scene_fps()
    ranges, current = timeline
    cmds.playbackOptions(**{flag: seconds * fps for flag, seconds in ranges.items()})
    cmds.currentTime(current * fps, edit=True)


class PreparedTiming:
    def __init__(self):
        self._unit = cmds.currentUnit(query=True, time=True)
        self._timeline = _timeline_seconds()
        self._playback = _playback_options(PLAYBACK_FLAGS)
        self._import_settings_saved = False
        self._scene_changed = False

    def _prepare(self):
        if not cmds.pluginInfo("fbxmaya", query=True, loaded=True):
            cmds.loadPlugin("fbxmaya", quiet=True)
        mel.eval("FBXPushSettings;")
        self._import_settings_saved = True
        mel.eval("FBXImportResamplingRateSource -v File;")
        mel.eval("FBXImportSetMayaFrameRate -v false;")
        self._scene_changed = True
        _set_timeline(SOURCE_UNIT, self._timeline)
        cmds.playbackOptions(playbackSpeed=1.0, by=1.0)

    def _restore_import_settings(self):
        if self._import_settings_saved:
            mel.eval("FBXPopSettings;")
            self._import_settings_saved = False

    def finish_import(self):
        """Restore FBX import options while ADV continues to use the 30 FPS timeline."""
        self._restore_import_settings()
        if self._scene_changed and abs(_scene_fps() - SOURCE_FPS) > 1e-8:
            raise RuntimeError("导入期间帧率发生了变化。请恢复后重新准备，再运行 Apply。")

    def restore(self, *, keep_motion_range=False):
        """Restore the prior timing. Keep the imported range in seconds when requested."""
        timeline = _timeline_seconds() if keep_motion_range and self._scene_changed else self._timeline
        try:
            self._restore_import_settings()
        finally:
            if self._scene_changed:
                _set_timeline(self._unit, timeline)
                cmds.playbackOptions(**self._playback)
                self._scene_changed = False

    def _scene_replaced(self):
        # The newly opened scene owns its time unit and range.
        self._scene_changed = False
        self._restore_import_settings()


def prepare():
    """Start a timing session before applying a FlowHMR FBX in AdvancedSkeleton."""
    timing = PreparedTiming()
    try:
        timing._prepare()
    except Exception:
        timing.restore()
        raise
    return timing


class TimingPanel:
    def __init__(self):
        self._timing = None
        self._applied = False
        self.window = cmds.window(WINDOW, title="FlowHMR · ADV 帧率", widthHeight=(380, 180),
                                  retain=True, maximizeButton=False, closeCommand=self._cancel)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=8, columnAttach=("both", 10))
        cmds.text(label="用于 FlowHMR 导出的 Maya AdvancedSkeleton FBX", align="left")
        self._status = cmds.text(label="", align="left", wordWrap=True, height=44)
        self._step = cmds.button(label="", command=self._advance, height=28)
        self._cancel_button = cmds.button(label="提前恢复", command=self._cancel, height=26)
        for event in ("NewSceneOpened", "SceneOpened"):
            cmds.scriptJob(event=(event, self._scene_replaced), parent=self.window)
        self._refresh()

    def _refresh(self, status=None):
        if self._timing is None:
            label, instruction = "准备 30 FPS", "先准备帧率，再到 ADV 的 Custom 动作库点 Apply。"
        elif not self._applied:
            label, instruction = "FBX 已应用", "在 ADV 中完成 Apply 后，点击下方按钮。"
        else:
            label, instruction = "烘焙完成，恢复原帧率", "在 ADV 中执行 Bake Body，保持 30 FPS 完成烘焙。"
        cmds.text(self._status, edit=True, label=status or instruction)
        cmds.button(self._step, edit=True, label=label)
        cmds.button(self._cancel_button, edit=True, enable=self._timing is not None)

    def _advance(self, *_):
        try:
            if self._timing is None:
                self._timing = prepare()
            elif not self._applied:
                self._timing.finish_import()
                self._applied = True
            else:
                self._restore()
                self._refresh("已恢复工程的帧率和播放设置。")
                return
            self._refresh()
        except RuntimeError as error:
            self._report_error(error)

    def _report_error(self, error):
        cmds.warning(f"FlowHMR — {error}")
        self._refresh(str(error))

    def _restore(self):
        if self._timing is not None:
            self._timing.restore(keep_motion_range=self._applied)
            self._timing = None
            self._applied = False

    def _cancel(self, *_):
        try:
            self._restore()
            self._refresh()
        except RuntimeError as error:
            self._report_error(error)

    def _scene_replaced(self, *_):
        if self._timing is not None:
            try:
                self._timing._scene_replaced()
                self._timing = None
                self._applied = False
                self._refresh()
            except RuntimeError as error:
                self._report_error(error)


def show_panel():
    """Open the optional timing panel without changing Maya settings."""
    window = WINDOW if cmds.window(WINDOW, exists=True) else TimingPanel().window
    cmds.showWindow(window)
    return window


def onMayaDroppedPythonFile(*_):
    show_panel()


if __name__ == "__main__":
    show_panel()
