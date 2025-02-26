from functools import partial
import weakref

import numpy as np
import pyqtgraph as pg
import pyqtgraph.functions as fn
from PySide6 import QtCore, QtGui, QtWidgets


class ViewBoxMenu(QtWidgets.QMenu):
    pan = "Pan mode"
    cursor = "Cursor mode"

    def __init__(self, view):
        super().__init__()

        self.view = weakref.ref(
            view
        )  ## keep weakref to view to avoid circular reference (don't know why, but this prevents the ViewBox from being collected)
        self.valid = False  ## tells us whether the ui needs to be updated
        self.viewMap = (
            weakref.WeakValueDictionary()
        )  ## weakrefs to all views listed in the link combos

        self.leftMenu = QtWidgets.QMenu("Mouse Mode")
        group = QtGui.QActionGroup(self)

        # This does not work! QAction _must_ be initialized with a permanent
        # object as the parent or else it may be collected prematurely.
        # pan = self.leftMenu.addAction("3 button", self.set3ButtonMode)
        # zoom = self.leftMenu.addAction("1 button", self.set1ButtonMode)
        pan = QtGui.QAction(ViewBoxMenu.pan, self.leftMenu)
        cursor = QtGui.QAction(ViewBoxMenu.cursor, self.leftMenu)
        zoom = QtGui.QAction("Rectangle selection mode", self.leftMenu)
        self.leftMenu.addAction(pan)
        self.leftMenu.addAction(cursor)
        # self.leftMenu.addAction(zoom)
        pan.triggered.connect(partial(self.set_mouse_mode, "pan"))
        cursor.triggered.connect(partial(self.set_mouse_mode, "cursor"))
        zoom.triggered.connect(partial(self.set_mouse_mode, "rect"))

        pan.setCheckable(True)
        cursor.setCheckable(True)
        zoom.setCheckable(True)
        pan.setActionGroup(group)
        cursor.setActionGroup(group)
        zoom.setActionGroup(group)
        self.mouseModes = [pan, cursor, zoom]
        self.addMenu(self.leftMenu)

        self.view().sigStateChanged.connect(self.viewStateChanged)

        self.updateState()

    def viewStateChanged(self):
        self.valid = False
        self.updateState()

    def updateState(self):
        state = self.view().getState(copy=False)
        if state["mouseMode"] == ViewBoxWithCursor.PanMode:
            self.mouseModes[0].setChecked(True)
        elif state["mouseMode"] == ViewBoxWithCursor.CursorMode:
            self.mouseModes[1].setChecked(True)
        else:
            self.mouseModes[2].setChecked(True)

        self.valid = True

    def popup(self, *args):
        if not self.valid:
            self.updateState()
        QtWidgets.QMenu.popup(self, *args)

    def set_mouse_mode(self, mode):
        self.view().setLeftButtonAction(mode)


class ViewBoxWithCursor(pg.ViewBox):
    PanMode = 3
    CursorMode = 2
    RectMode = 1

    sigCursorMoved = QtCore.Signal(object)
    sigZoomChanged = QtCore.Signal(object)
    sigZoomFinished = QtCore.Signal(object)

    X_zoom = QtCore.QKeyCombination(
        QtCore.Qt.ShiftModifier,
        QtCore.Qt.Key_Shift,
    ).toCombined()

    Y_zoom = QtCore.QKeyCombination(
        QtCore.Qt.AltModifier,
        QtCore.Qt.Key_Alt,
    ).toCombined()

    XY_zoom = (
        QtCore.QKeyCombination(
            QtCore.Qt.ShiftModifier | QtCore.Qt.AltModifier,
            QtCore.Qt.Key_Alt,
        ).toCombined(),
        QtCore.QKeyCombination(
            QtCore.Qt.ShiftModifier | QtCore.Qt.AltModifier,
            QtCore.Qt.Key_Shift,
        ).toCombined(),
    )

    def __init__(self, plot, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.menu.setParent(None)
        self.menu = None
        self.menu = ViewBoxMenu(self)

        self.zoom = None
        self.zoom_start = None
        self._matrixNeedsUpdate = True
        self.updateMatrix()

        self.cursor = None
        self.plot = plot

        self._settings = QtCore.QSettings()

    def __repr__(self):
        return "ASAM ViewBox"

    def setMouseMode(self, mode):
        """
        Set the mouse interaction mode. *mode* must be either ViewBoxWithCursor.PanMode, ViewBoxWithCursor.CursorMode or ViewBoxWithCursor.RectMode.
        In PanMode, the left mouse button pans the view and the right button scales.
        In RectMode, the left button draws a rectangle which updates the visible region (this mode is more suitable for single-button mice)
        """
        if mode not in [
            ViewBoxWithCursor.PanMode,
            ViewBoxWithCursor.CursorMode,
            ViewBoxWithCursor.RectMode,
        ]:
            raise Exception(
                "Mode must be ViewBoxWithCursor.PanMode, ViewBoxWithCursor.RectMode or ViewBoxWithCursor.CursorMode"
            )
        self.state["mouseMode"] = mode
        self.sigStateChanged.emit(self)

    def setLeftButtonAction(self, mode="rect"):  ## for backward compatibility
        if mode.lower() == "rect":
            self.setMouseMode(ViewBoxWithCursor.RectMode)
        elif mode.lower() == "pan":
            self.setMouseMode(ViewBoxWithCursor.PanMode)
        elif mode.lower() == "cursor":
            self.setMouseMode(ViewBoxWithCursor.CursorMode)
        else:
            raise Exception(
                f'graphicsItems:ViewBox:setLeftButtonAction: unknown mode = {mode} (Options are "pan", "cursor" and "rect")'
            )

    def mouseDragEvent(self, ev, axis=None, ignore_cursor=False):
        ## if axis is specified, event will only affect that axis.
        ev.accept()  ## we accept all buttons

        pos = ev.scenePos()
        dif = pos - ev.lastScenePos()
        dif = dif * -1

        ## Ignore axes if mouse is disabled
        mouseEnabled = np.array(self.state["mouseEnabled"], dtype=np.float64)
        mask = mouseEnabled.copy()
        if axis is not None:
            mask[1 - axis] = 0.0

        if (
            self.state["mouseMode"] == ViewBoxWithCursor.CursorMode
            and not ignore_cursor
        ):
            if ev.button() == QtCore.Qt.MouseButton.LeftButton:
                self.sigCursorMoved.emit(ev)
                if self.zoom_start is not None:
                    end = self.mapSceneToView(ev.scenePos())
                    self.sigZoomChanged.emit((self.zoom_start, end, self.zoom))
                    if ev.isFinish():
                        self.sigZoomFinished.emit((self.zoom_start, end, self.zoom))
                        self.zoom_start = None
                        self.sigZoomChanged.emit(None)

            else:
                tr = self.childGroup.transform()
                tr = fn.invertQTransform(tr)
                tr = tr.map(dif * mask) - tr.map(pg.Point(0, 0))

                x = tr.x() if mask[0] == 1 else None

                self._resetTarget()
                if x is not None:
                    self.translateBy(x=x, y=0)
                self.sigRangeChangedManually.emit(self.state["mouseEnabled"])

        else:
            ## Scale or translate based on mouse button
            if ev.button() in [
                QtCore.Qt.MouseButton.LeftButton,
                QtCore.Qt.MouseButton.MiddleButton,
            ]:
                tr = self.childGroup.transform()
                tr = fn.invertQTransform(tr)
                tr = tr.map(dif * mask) - tr.map(pg.Point(0, 0))

                x = tr.x() if mask[0] == 1 else None
                y = tr.y() if mask[1] == 1 else None

                self._resetTarget()
                if x is not None or y is not None:
                    self.translateBy(x=x, y=y)
                self.sigRangeChangedManually.emit(self.state["mouseEnabled"])

            elif ev.button() & QtCore.Qt.MouseButton.RightButton:
                if self.state["aspectLocked"] is not False:
                    mask[0] = 0

                dif = ev.screenPos() - ev.lastScreenPos()
                dif = np.array([dif.x(), dif.y()])
                dif[0] *= -1
                s = ((mask * 0.02) + 1) ** dif

                tr = self.childGroup.transform()
                tr = fn.invertQTransform(tr)

                x = s[0] if mouseEnabled[0] == 1 else None
                y = s[1] if mouseEnabled[1] == 1 else None

                center = pg.Point(
                    tr.map(ev.buttonDownPos(QtCore.Qt.MouseButton.RightButton))
                )
                self._resetTarget()
                self.scaleBy(x=x, y=y, center=center)
                self.sigRangeChangedManually.emit(self.state["mouseEnabled"])

    def keyPressEvent(self, ev):
        if self.zoom_start is None:
            self.zoom = ev.keyCombination().toCombined()
        ev.ignore()

    def keyReleaseEvent(self, ev):
        if self.zoom_start is None:
            self.zoom = None
        ev.ignore()

    def mousePressEvent(self, ev):
        if self.state["mouseMode"] == ViewBoxWithCursor.CursorMode and self.zoom in (
            self.X_zoom,
            self.Y_zoom,
            *self.XY_zoom,
        ):
            self.zoom_start = self.mapSceneToView(ev.scenePos())

        ev.ignore()

    def updateScaleBox(self, p1, p2):
        r = QtCore.QRectF(p1, p2)
        r = self.childGroup.mapRectFromScene(r)
        self.rbScaleBox.setPos(r.topLeft())
        tr = QtGui.QTransform.fromScale(r.width(), r.height())
        self.rbScaleBox.setTransform(tr)
        self.rbScaleBox.show()

    def wheelEvent(self, ev, axis=None):
        if self.state["mouseMode"] == ViewBoxWithCursor.CursorMode:
            mask = [True, False]
        else:
            if axis in (0, 1):
                mask = [False, False]
                mask[axis] = self.state["mouseEnabled"][axis]
            else:
                mask = self.state["mouseEnabled"][:]

        if (
            self._settings.value("zoom_x_center_on_cursor", True, type=bool)
            and self.cursor is not None
            and self.cursor.isVisible()
        ):
            x_range, _ = self.viewRange()
            delta = x_range[1] - x_range[0]

            # mouse scroll wheel has 120 delta for each step
            # touchpad has much lower delta values but is triggerd more often
            event_delta = ev.delta() * 0.15 / 120

            step = -delta * event_delta

            pos = self.cursor.value()
            x_range = pos - delta / 2, pos + delta / 2
            self.setXRange(x_range[0] - step, x_range[1] + step, padding=0)

        else:
            pos = ev.pos()

            s = 1.02 ** (
                ev.delta() * self.state["wheelScaleFactor"]
            )  # actual scaling factor

            s = [(None if m is False else s) for m in mask]
            center = pg.Point(fn.invertQTransform(self.childGroup.transform()).map(pos))

            self._resetTarget()
            self.scaleBy(s, center)

        ev.accept()
        self.sigRangeChangedManually.emit(mask)
