import os
from setuptools import setup, find_packages

VERSION = '0.0.0'
version_env = os.environ.get('VERSION')
if version_env:
    VERSION = version_env

setup(
       name = 'psdaq',
       license = 'LCLS II',
       description = 'LCLS II DAQ package',
       version = VERSION,
       packages = find_packages(),
       package_data={'psdaq.control_gui': ['data/icons/*.png','data/icons/*.gif'],},

       scripts = ['psdaq/procmgr/procmgr','psdaq/procmgr/procstat','psdaq/procmgr/condaProcServ'],

       entry_points={
            'console_scripts': [
                'control = psdaq.control.control:main',
                'selectPlatform = psdaq.control.selectPlatform:main',
                'showPlatform = psdaq.control.showPlatform:main',
                'daqstate = psdaq.control.daqstate:main',
                'currentexp = psdaq.control.currentexp:main',
                'testClient2 = psdaq.control.testClient2:main',
                'testAsyncErr = psdaq.control.testAsyncErr:main',
                'testFileReport = psdaq.control.testFileReport:main',
                'configdb = psdaq.configdb.configdb:main',
                'epixquad_store_gainmap = psdaq.configdb.epixquad_store_gainmap:main',
                'epixquad_create_pixelmask = psdaq.configdb.epixquad_create_pixelmask:main',
                'getrun = psdaq.control.getrun:main',
                'groupca = psdaq.cas.groupca:main',
                'partca = psdaq.cas.partca:main',
                'xpmpva = psdaq.cas.xpmpva:main',
                'deadca = psdaq.cas.deadca:main',
                'dtica = psdaq.cas.dtica:main',
                'dticas = psdaq.cas.dticas:main',
                'hsdca = psdaq.cas.hsdca:main',
                'hsdcas = psdaq.cas.hsdcas:main',
                'hsdpva = psdaq.cas.hsdpva:main',
                'hsdpvs = psdaq.cas.hsdpvs:main',
                'pvatable = psdaq.cas.pvatable:main',
                'pvant = psdaq.cas.pvant:main',
                'campvs = psdaq.cas.campvs:main',
                'tprca = psdaq.cas.tprca:main',
                'tprcas = psdaq.cas.tprcas:main',
                'xpmioc = psdaq.cas.xpmioc:main',
                'bldcas = psdaq.cas.bldcas:main',
                'hpsdbuscas = psdaq.cas.hpsdbuscas:main',
                'wave8pvs = psdaq.cas.wave8pvs:main',
                'pyxpm = psdaq.pyxpm.pyxpm:main',
                'amccpromload = psdaq.pyxpm.amccpromload:main',
                'pykcu = psdaq.pykcu.pykcu:main',
                'control_gui = psdaq.control_gui.app.control_gui:control_gui',
                'bluesky_simple = psdaq.control.bluesky_simple:main',
                'opal_config_scan = psdaq.control.opal_config_scan:main',
                'ts_config_scan = psdaq.control.ts_config_scan:main',
                'epics_exporter = psdaq.cas.epics_exporter:main',
                'seqplot = psdaq.seq.seqplot:main',
                'seqprogram = psdaq.seq.seqprogram:main',
              ]
       },
)
