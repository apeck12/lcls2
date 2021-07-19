
"""Class :py:class:`H5VControl` is a QWidget with control fields
===================================================================
Usage ::

    # Run test: python lcls2/psana/psana/graphqt/H5VControl.py

    from psana.graphqt.H5VControl import H5VControl
    w = H5VControl()

Created on 2020-01-04 by Mikhail Dubrovin
"""
import logging
#logger = logging.getLogger(__name__)

import sys
from PyQt5.QtWidgets import QWidget, QLabel, QComboBox, QPushButton, QHBoxLayout, QLineEdit
from PyQt5.QtCore import QSize
from psana.graphqt.H5VConfigParameters import cp
from psana.graphqt.Styles import style
from psana.graphqt.QWIcons import icon
from psana.graphqt.QWFileNameV2 import QWFileNameV2
#from psana.graphqt.QWFileName import QWFileName


class H5VControl(QWidget):
    """QWidget for H5V control fields"""

    def __init__(self, **kwargs):

        parent = kwargs.get('parent',None)

        QWidget.__init__(self, parent)
        #self._name = 'H5VControl'
        self.lab_ctrl = QLabel('Control:')

        self.but_exp_col  = QPushButton('Collapse')

        fname = cp.h5vmain.wtree.fname if cp.h5vmain is not None else './test.h5'

        self.w_fname = QWFileNameV2(None, label='HDF5 file:',\
           path=fname, fltr='*.h5 *.hdf5 \n*')

        self.hbox = QHBoxLayout() 
        self.hbox.addWidget(self.lab_ctrl)
        self.hbox.addWidget(self.but_exp_col)
        self.hbox.addStretch(1) 
        self.hbox.addWidget(self.w_fname)
        #self.hbox.addSpacing(20)

        #self.hbox.addLayout(self.grid)
        self.setLayout(self.hbox)
 
        #self.but_exp_col.clicked.connect(self.on_but_clicked)
        self.but_exp_col.clicked.connect(self.on_but_exp_col)

        if cp.h5vmain is not None:
            self.w_fname.connect_path_is_changed_to_recipient(cp.h5vmain.wtree.set_file)

        self.set_tool_tips()
        self.set_style()
        #self.set_buttons_visiable()


    def set_tool_tips(self):
        self.setToolTip('Control fields/buttons')


    def set_style(self):
        #self.         setStyleSheet(style.styleBkgd)
        #self.lab_db_filter.setStyleSheet(style.styleLabel)
        self.lab_ctrl.setStyleSheet(style.styleLabel)
        icon.set_icons()
        self.but_exp_col.setIcon(icon.icon_folder_open)


    def on_but_exp_col(self):
        if cp.h5vmain is None: return

        wtree = cp.h5vmain.wtree
        but = self.but_exp_col
        if but.text() == 'Expand':
            wtree.process_expand()
            self.but_exp_col.setIcon(icon.icon_folder_closed)
            but.setText('Collapse')
        else:
            wtree.process_collapse()
            self.but_exp_col.setIcon(icon.icon_folder_open)
            but.setText('Expand')


#    def on_but_clicked(self):
#        for but in self.list_of_buts:
#            if but.hasFocus(): break
#        logger.info('Click on "%s"' % but.text())
#        if   but == self.but_exp_col : self.expand_collapse_dbtree()
#        elif but == self.but_tabs    : self.view_hide_tabs()
#        #elif but == self.but_level   : self.set_logger_level()


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys
    app = QApplication(sys.argv)
    w = H5VControl()
    #w.setGeometry(200, 400, 500, 200)
    w.setWindowTitle('H5V Control Panel')
    w.show()
    app.exec_()
    del w
    del app

# EOF
