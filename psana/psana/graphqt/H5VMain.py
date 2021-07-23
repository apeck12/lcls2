
"""Class :py:class:`H5VMain` is a QWidget for main window of hdf5viewer 
========================================================================

Usage ::

    # Run test: python lcls2/psana/psana/graphqt/H5VMain.py

    from psana.graphqt.H5VMain import H5VMain

See method: hdf5explorer

Created on 2019-11-12 by Mikhail Dubrovin
"""

import logging
#logger = logging.getLogger(__name__)

import sys
from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout, QSplitter, QTextEdit
from psana.graphqt.QWLoggerStd import QWLoggerStd#, QWFilter

from psana.graphqt.H5VControl import H5VControl
from psana.graphqt.H5VQWTree import Qt, H5VQWTree
from psana.graphqt.CMConfigParameters import cp
from psana.pyalgos.generic.Utils import print_kwargs, is_in_command_line


class H5VMain(QWidget):

    def __init__(self, **kwargs):
        QWidget.__init__(self, parent=None)
        #self._name = self.__class__.__name__

        cp.h5vmain = self

        self.proc_kwargs(**kwargs)

        kwargs['parent'] = self

        if self.wlog is None: self.wlog = QWLoggerStd(cp, show_buttons=False)
        self.wtree = H5VQWTree(**kwargs)
        self.wctrl = H5VControl(**kwargs)
        #self.wtext = QTextEdit('Some text')
        self.wtree.wctrl = self.wctrl

        self.hspl = QSplitter(Qt.Horizontal)
        self.hspl.addWidget(self.wtree)
        self.hspl.addWidget(self.wlog)
        #self.hspl.addWidget(self.wtext)

        #self.hbox = QHBoxLayout() 
        #self.hbox.addWidget(self.hspl)

        self.vbox = QVBoxLayout() 
        self.vbox.addWidget(self.wctrl)
        self.vbox.addWidget(self.hspl)
        #self.vbox.addLayout(self.hspl)

        self.setLayout(self.vbox)

        self.set_style()
        self.set_tool_tips()

        #self.connect_signals_to_slots()


    def proc_kwargs(self, **kwargs):
        print_kwargs(kwargs)
        loglevel   = kwargs.get('loglevel', 'DEBUG').upper()
        logdir     = kwargs.get('logdir', './')
        savelog    = kwargs.get('savelog', False)
        self.wlog  = kwargs.get('wlog', None)
        if is_in_command_line('-l', '--loglevel'): cp.log_level.setValue(loglevel)
        #if is_in_command_line('-S', '--saveloglogdir'):
        #if is_in_command_line('-L', '--logdir'):
        cp.log_prefix.setValue(logdir)
        cp.save_log_at_exit.setValue(savelog)


    def connect_signals_to_slots(self):
        pass
        #self.connect(self.wbut.but_reset, QtCore.SIGNAL('clicked()'), self.on_but_reset)
        #self.connect(self.wbut.but_save,  QtCore.SIGNAL('clicked()'), self.on_but_save)


    def set_tool_tips(self):
        self.setToolTip('hdf5 explorer')


    def set_style(self):
        self.setGeometry(50, 50, 500, 600)
        #self.setGeometry(self.main_win_pos_x .value(),\
        #                 self.main_win_pos_y .value(),\
        #                 self.main_win_width .value(),\
        #                 self.main_win_height.value())
        #w_height = self.main_win_height.value()

        #self.setMinimumSize(500, 400)

        #w = self.main_win_width.value()

        self.layout().setContentsMargins(0,0,0,0)

        self.wlog.setMinimumWidth(500)

        self.wctrl.setFixedHeight(50)
        #self.wctrl.setMaximumHeight(80)

        #spl_pos = cp.main_vsplitter.value()
        #self.vspl.setSizes((spl_pos,w_height-spl_pos,))

        #self.wrig.setMinimumWidth(350)
        #self.wrig.setMaximumWidth(450)

        #self.wrig.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        #self.hspl.moveSplitter(w*0.5,0)

        #self.setFixedSize(800,500)
        #self.setMinimumSize(500,800)

        #self.butELog.setStyleSheet(style.styleButton)
        #self.butFile.setStyleSheet(style.styleButton)

        #self.butELog    .setVisible(False)
        #self.butFBrowser.setVisible(False)

        #self.but1.raise_()

    def closeEvent(self, e):
        #logger.debug('closeEvent')
        QWidget.closeEvent(self, e)

        cp.h5vmain = None

        #try   : self.gui_win.close()
        #except: pass

        #try   : del self.gui_win
        #except: pass



def hdf5explorer(**kwargs):
    import os
    os.environ['LIBGL_ALWAYS_INDIRECT'] = '1'
    #fmt = '%(asctime)s %(name)s %(levelname)s: %(message)s'
    #logging.basicConfig(format=fmt, datefmt='%H:%M:%S', level=logging.DEBUG)

    a = QApplication(sys.argv)
    w = H5VMain(**kwargs)
    w.setGeometry(10, 25, 900, 700)
    w.setWindowTitle('HDF5 explorer')
    w.move(50,20)
    w.show()
    a.exec_()
    del w
    del a


if __name__ == "__main__":
    import os
    kwargs = {\
      'fname':'/reg/g/psdm/detector/calib/jungfrau/jungfrau-171113-154920171025-3d00fb.h5',\
      'loglevel':'INFO',\
      'logdir':'%s/hdf5explorer-log' % os.path.expanduser('~'),\
      'savelog':True}
    hdf5explorer(**kwargs)

# EOF
