"""
The Python3 mode for the Mu editor.

Copyright (c) 2015-2017 Nicholas H.Tollervey and others (see the AUTHORS file).

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import sys
import os
import logging
from mu.modes.base import BaseMode
from mu.modes.api import PYTHON3_APIS, SHARED_APIS, PI_APIS
from mu.logic import write_and_flush
from mu.resources import load_icon
from qtconsole.manager import QtKernelManager
from qtconsole.client import QtKernelClient
from PyQt5.QtCore import QObject, QThread, pyqtSignal


logger = logging.getLogger(__name__)


class KernelRunner(QObject):
    """
    Used to control the iPython kernel in a non-blocking manner so the UI
    remains responsive.
    """
    kernel_started = pyqtSignal(QtKernelManager, QtKernelClient)
    kernel_finished = pyqtSignal()

    def __init__(self, cwd):
        """
        Initialise the kernel runner with a target current working directory.
        """
        super().__init__()
        self.cwd = cwd

    def start_kernel(self):
        """
        Start the kernel, obtain a client and emit a signal when both are
        started.
        """
        logger.info(sys.path)
        os.chdir(self.cwd)  # Ensure the kernel runs with the expected CWD.
        self.repl_kernel_manager = QtKernelManager()
        self.repl_kernel_manager.start_kernel()
        self.repl_kernel_client = self.repl_kernel_manager.client()
        self.kernel_started.emit(self.repl_kernel_manager,
                                 self.repl_kernel_client)

    def stop_kernel(self):
        """
        Stop the client connections to the kernel, affect an immediate
        shutdown of the kernel and emit a "finished" signal.
        """
        self.repl_kernel_client.stop_channels()
        self.repl_kernel_manager.shutdown_kernel(now=True)
        self.kernel_finished.emit()


class PythonMode(BaseMode):
    """
    Represents the functionality required by the Python 3 mode.
    """

    name = _('Python 3')
    description = _('Create code using standard Python 3.')
    icon = 'python'
    runner = None
    has_debugger = True
    kernel_runner = None
    stop_kernel = pyqtSignal()

    def actions(self):
        """
        Return an ordered list of actions provided by this module. An action
        is a name (also used to identify the icon) , description, and handler.
        """
        return [
            {
                'name': 'run',
                'display_name': _('Run'),
                'description': _('Run your Python script.'),
                'handler': self.run_toggle,
                'shortcut': 'F5',
            },
            {
                'name': 'debug',
                'display_name': _('Debug'),
                'description': _('Debug your Python script.'),
                'handler': self.debug,
                'shortcut': 'F6',
            },
            {
                'name': 'repl',
                'display_name': _('REPL'),
                'description': _('Use the REPL for live coding.'),
                'handler': self.toggle_repl,
                'shortcut': 'Ctrl+Shift+I',
            },
        ]

    def api(self):
        """
        Return a list of API specifications to be used by auto-suggest and call
        tips.
        """
        return SHARED_APIS + PYTHON3_APIS + PI_APIS

    def run_toggle(self, event):
        """
        Handles the toggling of the run button to start/stop a script.
        """
        run_slot = self.view.button_bar.slots['run']
        if self.runner:
            self.stop_script()
            run_slot.setIcon(load_icon('run'))
            run_slot.setText(_('Run'))
            run_slot.setToolTip(_('Run your Python script.'))
            self.view.button_bar.slots['debug'].setEnabled(True)
            self.view.button_bar.slots['modes'].setEnabled(True)
        else:
            self.run_script()
            if self.runner:
                # If the script started, toggle the button state. See #338.
                run_slot.setIcon(load_icon('stop'))
                run_slot.setText(_('Stop'))
                run_slot.setToolTip(_('Stop your Python script.'))
                self.view.button_bar.slots['debug'].setEnabled(False)
                self.view.button_bar.slots['modes'].setEnabled(False)

    def run_script(self):
        """
        Run the current script.
        """
        # Grab the Python file.
        tab = self.view.current_tab
        if tab is None:
            logger.debug('There is no active text editor.')
            self.stop_script()
            return
        if tab.path is None:
            # Unsaved file.
            self.editor.save()
        if tab.path:
            # If needed, save the script.
            if tab.isModified():
                with open(tab.path, 'w', newline='') as f:
                    logger.info('Saving script to: {}'.format(tab.path))
                    logger.debug(tab.text())
                    write_and_flush(f, tab.text())
                    tab.setModified(False)
            logger.debug(tab.text())
            self.runner = self.view.add_python3_runner(tab.path,
                                                       self.workspace_dir(),
                                                       interactive=True)
            self.runner.process.waitForStarted()

    def stop_script(self):
        """
        Stop the currently running script.
        """
        logger.debug('Stopping script.')
        if self.runner:
            self.runner.process.kill()
            self.runner.process.waitForFinished()
            self.runner = None
        self.view.remove_python_runner()

    def debug(self, event):
        """
        Debug the script using the debug mode.
        """
        logger.info("Starting debug mode.")
        self.editor.change_mode('debugger')
        self.editor.mode = 'debugger'
        self.editor.modes['debugger'].start()

    def toggle_repl(self, event):
        """
        Toggles the REPL on and off
        """
        if self.kernel_runner is None:
            logger.info('Toggle REPL on.')
            self.editor.show_status_message(_("Starting iPython REPL."))
            self.add_repl()
        else:
            logger.info('Toggle REPL off.')
            self.editor.show_status_message(_("Stopping iPython REPL "
                                              "(this may take a short amount "
                                              "of time)."))
            self.remove_repl()

    def add_repl(self):
        """
        Create a new Jupyter REPL session in a non-blocking way.
        """
        self.view.button_bar.slots['repl'].setEnabled(False)
        self.kernel_thread = QThread()
        self.kernel_runner = KernelRunner(cwd=self.workspace_dir())
        self.kernel_runner.moveToThread(self.kernel_thread)
        self.kernel_runner.kernel_started.connect(self.on_kernel_start)
        self.kernel_runner.kernel_finished.connect(self.kernel_thread.quit)
        self.stop_kernel.connect(self.kernel_runner.stop_kernel)
        self.kernel_thread.started.connect(self.kernel_runner.start_kernel)
        self.kernel_thread.finished.connect(self.on_kernel_stop)
        self.kernel_thread.start()

    def remove_repl(self):
        """
        Remove the Jupyter REPL session.
        """
        self.view.remove_repl()
        self.view.button_bar.slots['repl'].setEnabled(False)
        # Don't block the GUI
        self.stop_kernel.emit()

    def on_kernel_start(self, kernel_manager, kernel_client):
        """
        Handles UI update when the kernel runner has started the iPython
        kernel.
        """
        self.view.add_jupyter_repl(kernel_manager, kernel_client)
        self.view.button_bar.slots['repl'].setEnabled(True)
        self.editor.show_status_message(_("REPL started."))

    def on_kernel_stop(self):
        """
        Handles UI updates for when the kernel runner has shut down the running
        iPython kernel.
        """
        self.repl_kernel_manager = None
        if 'repl' in self.view.button_bar.slots:
            self.view.button_bar.slots['repl'].setEnabled(True)
        self.editor.show_status_message(_("REPL stopped."))
        self.kernel_runner = None
