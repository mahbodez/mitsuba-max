"""The render window: a PySide6 dialog parented to Max.

Parented, not floating. An orphan top-level window disappears behind Max the first time the
user clicks the viewport, and on Windows it will not be restored with the application.
Probe 01b confirmed the bridge: PySide6 6.8.3, `shiboken6` importable under that name, and
`rt.windows.getMAXHWND()` returning a valid handle. `rt.GetQMaxMainWindow()` does **not**
exist — it was a MaxPlus-era API — so anything suggesting it is out of date.

Nothing here blocks. The worker is polled on a `QTimer` at 10 Hz; every read of the shared
film is allowed to come back empty, and the correct response is to skip the repaint and try
again in 100 ms.
"""

import time
from pathlib import Path

import shiboken6
from pymxs import runtime as rt
from PySide6 import QtCore, QtGui, QtWidgets

from core import protocol as p
from core.emit_xml import scene_to_xml
from core.ir import Scene
from max_side.client import WorkerClient, WorkerCrashed
from max_side.numpy_bridge import ensure_numpy
from max_side.settings import Settings, save

__all__ = ["RenderWindow", "max_main_window"]

POLL_MS = 100
"""10 Hz. Fast enough that Cancel feels instant, slow enough to cost nothing."""


def max_main_window() -> QtWidgets.QWidget:
    """Max's main window as a `QWidget`, for use as a parent."""
    return shiboken6.wrapInstance(int(rt.windows.getMAXHWND()), QtWidgets.QWidget)


class ImageView(QtWidgets.QScrollArea):
    """Zoom and pan over a `QImage`. Deliberately simple.

    `Qt.SmoothTransformation` is used when zoomed out and nearest-neighbour when zoomed in,
    because at 4x an artist is inspecting individual pixels for noise and interpolation
    hides exactly what they are looking for.
    """

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._label = QtWidgets.QLabel()
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._label.setBackgroundRole(QtGui.QPalette.ColorRole.Base)
        self.setWidget(self._label)
        self.setWidgetResizable(False)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._zoom = 1.0
        self._pixmap: QtGui.QPixmap | None = None
        self._panning_from: QtCore.QPoint | None = None

    def set_image(self, image: QtGui.QImage) -> None:
        self._pixmap = QtGui.QPixmap.fromImage(image)
        self._rescale()

    def set_zoom(self, zoom: float) -> None:
        self._zoom = max(0.05, min(16.0, zoom))
        self._rescale()

    def zoom(self) -> float:
        return self._zoom

    def _rescale(self) -> None:
        if self._pixmap is None:
            return
        size = self._pixmap.size() * self._zoom
        mode = (QtCore.Qt.TransformationMode.SmoothTransformation if self._zoom < 1.0
                else QtCore.Qt.TransformationMode.FastTransformation)
        self._label.setPixmap(self._pixmap.scaled(
            size, QtCore.Qt.AspectRatioMode.KeepAspectRatio, mode))
        self._label.resize(size)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        steps = event.angleDelta().y() / 120.0
        self.set_zoom(self._zoom * (1.25 ** steps))
        event.accept()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.MiddleButton:
            self._panning_from = event.pos()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._panning_from is not None:
            delta = event.pos() - self._panning_from
            self._panning_from = event.pos()
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        self._panning_from = None
        self.unsetCursor()
        super().mouseReleaseEvent(event)


class RenderWindow(QtWidgets.QDialog):
    """Progressive preview, live exposure controls, warnings panel, save buttons."""

    def __init__(self, client: WorkerClient, settings: Settings,
                 parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent or max_main_window())
        self.setWindowTitle("Mitsuba")
        self.setWindowFlag(QtCore.Qt.WindowType.Window, True)
        self.resize(1100, 780)

        self._client = client
        self._settings = settings
        self._np = ensure_numpy(Path(client.interpreter))
        self._scene: Scene | None = None
        self._scene_root: Path | None = None
        self._buffer = None
        self._started_at = 0.0
        self._job: int | None = None

        self._build_ui()

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # -- construction ------------------------------------------------------------------

    def _build_ui(self) -> None:
        self._view = ImageView(self)

        self._progress = QtWidgets.QProgressBar()
        self._progress.setRange(0, 100)
        self._status = QtWidgets.QLabel("idle")
        self._env_label = QtWidgets.QLabel("")
        self._env_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

        self._exposure = self._slider(-8.0, 8.0, self._settings.exposure, 0.05)
        self._gamma = self._slider(1.0, 3.0, self._settings.gamma, 0.01)
        self._exposure.valueChanged.connect(self._repaint_from_cache)
        self._gamma.valueChanged.connect(self._repaint_from_cache)

        self._cancel = QtWidgets.QPushButton("Cancel")
        self._cancel.clicked.connect(self._on_cancel)
        self._cancel.setEnabled(False)

        save_exr = QtWidgets.QPushButton("Save EXR")
        save_exr.clicked.connect(self._on_save_exr)
        save_png = QtWidgets.QPushButton("Save PNG")
        save_png.clicked.connect(self._on_save_png)
        save_xml = QtWidgets.QPushButton("Save scene.xml")
        save_xml.clicked.connect(self._on_save_xml)

        self._warnings = QtWidgets.QPlainTextEdit()
        self._warnings.setReadOnly(True)
        self._warnings.setMaximumHeight(150)
        self._warnings_box = QtWidgets.QGroupBox("Warnings")
        self._warnings_box.setCheckable(True)
        self._warnings_box.setChecked(False)
        self._warnings.setVisible(False)
        self._warnings_box.toggled.connect(self._warnings.setVisible)
        warn_layout = QtWidgets.QVBoxLayout(self._warnings_box)
        warn_layout.addWidget(self._warnings)

        controls = QtWidgets.QGridLayout()
        controls.addWidget(QtWidgets.QLabel("Exposure"), 0, 0)
        controls.addWidget(self._exposure, 0, 1)
        self._exposure_value = QtWidgets.QLabel("0.00 EV")
        controls.addWidget(self._exposure_value, 0, 2)
        controls.addWidget(QtWidgets.QLabel("Gamma"), 1, 0)
        controls.addWidget(self._gamma, 1, 1)
        self._gamma_value = QtWidgets.QLabel("2.20")
        controls.addWidget(self._gamma_value, 1, 2)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self._cancel)
        buttons.addStretch(1)
        buttons.addWidget(save_exr)
        buttons.addWidget(save_png)
        buttons.addWidget(save_xml)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(self._view, 1)
        layout.addWidget(self._progress)
        layout.addWidget(self._status)
        layout.addWidget(self._env_label)
        layout.addLayout(controls)
        layout.addWidget(self._warnings_box)
        layout.addLayout(buttons)

    @staticmethod
    def _slider(low: float, high: float, value: float, step: float) -> QtWidgets.QSlider:
        """A float slider built on Qt's integer one, in units of `step`."""
        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        slider.setRange(int(low / step), int(high / step))
        slider.setValue(int(value / step))
        slider.setProperty("mmx_step", step)
        return slider

    @staticmethod
    def _slider_value(slider: QtWidgets.QSlider) -> float:
        return slider.value() * float(slider.property("mmx_step"))

    # -- rendering ---------------------------------------------------------------------

    def start_render(self, scene: Scene, scene_root: Path, film_path: Path) -> None:
        """Submit a job and switch the window into progressive mode."""
        self._scene = scene
        self._scene_root = scene_root
        self._buffer = None
        self._started_at = time.perf_counter()

        # The camera's photographic settings set where the slider starts; they are never
        # baked into the render, so moving the slider afterwards stays instant.
        if scene.camera.exposure_scale > 0.0:
            import math
            stops = math.log2(scene.camera.exposure_scale)
            self._exposure.setValue(int(stops / float(self._exposure.property("mmx_step"))))

        self._show_warnings(scene)
        self._progress.setRange(0, scene.settings.passes)
        self._progress.setValue(0)
        self._cancel.setEnabled(True)
        self._status.setText("submitting…")
        try:
            self._job = self._client.submit(scene, film_path=film_path,
                                            scene_root=scene_root)
        except WorkerCrashed as exc:
            self._fail(str(exc))

    def _show_warnings(self, scene: Scene) -> None:
        if not scene.warnings:
            self._warnings_box.setTitle("Warnings — none")
            self._warnings.setPlainText("")
            return
        self._warnings_box.setTitle(f"Warnings — {len(scene.warnings)}")
        self._warnings.setPlainText("\n".join(
            f"[{w.category}] {w.node}: {w.reason}" for w in scene.warnings
        ))

    def _on_cancel(self) -> None:
        self._status.setText(
            "cancelling — takes effect at the end of the current pass "
            "(mi.render cannot be interrupted mid-call)"
        )
        self._client.cancel(self._job)

    # -- polling -----------------------------------------------------------------------

    def _tick(self) -> None:
        try:
            events = self._client.poll_events()
        except WorkerCrashed as exc:
            self._fail(str(exc))
            return

        repaint = False
        for event in events:
            match event:
                case p.Ready():
                    self._env_label.setText(
                        f"Mitsuba {event.mitsuba}  ·  variant {event.variant}"
                        + (f"  ·  scene scale {self._scene.scene_scale_to_meters:g} m/unit"
                           if self._scene else "")
                    )
                    self.setWindowTitle(f"Mitsuba — {event.variant}")
                case p.PassEv():
                    self._progress.setValue(event.index)
                    self._status.setText(
                        f"pass {event.index}  ·  {event.spp_done} spp  ·  "
                        f"{event.elapsed_s:.1f} s"
                    )
                    repaint = True
                case p.Done():
                    self._cancel.setEnabled(False)
                    verb = "cancelled" if event.cancelled else "done"
                    self._status.setText(
                        f"{verb}  ·  {event.spp_done} spp  ·  {event.elapsed_s:.1f} s"
                    )
                    repaint = True
                case p.ErrorEv():
                    self._fail(f"{event.message}\n\n{event.traceback}")
                    return
                case p.LogEv():
                    self._status.setText(event.message)

        if repaint:
            self._refresh_buffer()

    def _refresh_buffer(self) -> None:
        got = self._client.read_film()
        if got is None:
            return   # mid-write or no film yet; the next tick will pick it up
        self._buffer, _ = got
        self._repaint_from_cache()

    def _repaint_from_cache(self) -> None:
        """Re-tonemap the cached buffer. No renderer involvement, so this is instant."""
        exposure = self._slider_value(self._exposure)
        gamma = self._slider_value(self._gamma)
        self._exposure_value.setText(f"{exposure:+.2f} EV")
        self._gamma_value.setText(f"{gamma:.2f}")
        if self._buffer is None:
            return

        from core.tonemap import tonemap

        rgb = tonemap(self._buffer, exposure=exposure, gamma=gamma)
        height, width, _ = rgb.shape
        image = QtGui.QImage(rgb.data, width, height, 3 * width,
                             QtGui.QImage.Format.Format_RGB888)
        # QImage does not copy the buffer, and `rgb` is a local about to be collected.
        self._view.set_image(image.copy())

    def _fail(self, message: str) -> None:
        self._cancel.setEnabled(False)
        self._status.setText("failed")
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("Mitsuba")
        box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
        box.setText("The render failed.")
        # Copyable, never a summary: the real text is the only thing that helps.
        box.setDetailedText(message)
        box.exec()

    # -- saving ------------------------------------------------------------------------

    def _on_save_png(self) -> None:
        if self._buffer is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save PNG", "", "PNG (*.png)")
        if not path:
            return
        from core.tonemap import tonemap

        rgb = tonemap(self._buffer, exposure=self._slider_value(self._exposure),
                      gamma=self._slider_value(self._gamma))
        height, width, _ = rgb.shape
        QtGui.QImage(rgb.data, width, height, 3 * width,
                     QtGui.QImage.Format.Format_RGB888).copy().save(path)

    def _on_save_exr(self) -> None:
        """Write the linear buffer, unmodified by the display controls.

        Exposure and gamma are a *view*, so baking them into an EXR would silently destroy
        the reason someone chose EXR. Written with a minimal uncompressed writer rather than
        through Mitsuba, because `mitsuba` must never be imported into Max's process.
        """
        if self._buffer is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save EXR", "", "OpenEXR (*.exr)")
        if not path:
            return
        from max_side.exr import write_exr

        write_exr(Path(path), self._buffer[..., :3])
        self._status.setText(f"saved {path}")

    def _on_save_xml(self) -> None:
        if self._scene is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save scene.xml", "",
                                                        "Mitsuba scene (*.xml)")
        if not path:
            return
        Path(path).write_text(scene_to_xml(self._scene), encoding="utf-8")
        self._status.setText(f"saved {path}")

    # -- teardown ----------------------------------------------------------------------

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._timer.stop()
        self._settings.exposure = self._slider_value(self._exposure)
        self._settings.gamma = self._slider_value(self._gamma)
        save(self._settings)
        super().closeEvent(event)
