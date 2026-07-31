import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME = 'org.vibeos.Shell';
const OBJECT_PATH = '/org/vibeos/Shell';

const XML = `
<node>
  <interface name="org.vibeos.Shell">
    <method name="ListWindows">
      <arg type="s" name="windows" direction="out"/>
    </method>
    <method name="FocusWindow">
      <arg type="s" name="window_id" direction="in"/>
      <arg type="s" name="result" direction="out"/>
    </method>
    <method name="MinimizeWindow">
      <arg type="s" name="window_id" direction="in"/>
      <arg type="s" name="result" direction="out"/>
    </method>
    <method name="MaximizeWindow">
      <arg type="s" name="window_id" direction="in"/>
      <arg type="s" name="result" direction="out"/>
    </method>
    <method name="CloseWindow">
      <arg type="s" name="window_id" direction="in"/>
      <arg type="s" name="result" direction="out"/>
    </method>
    <method name="SetClipboard">
      <arg type="s" name="text" direction="in"/>
      <arg type="s" name="result" direction="out"/>
    </method>
    <method name="GetClipboard">
      <arg type="s" name="text" direction="out"/>
    </method>
  </interface>
</node>`;

export default class VibeOSExtension extends Extension {
    enable() {
        this._nextWindowId = 1;
        this._windowIds = new Map();
        this._windowsById = new Map();
        this._dbus = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._dbus.export(Gio.DBus.session, OBJECT_PATH);
        this._nameId = Gio.bus_own_name_on_connection(
            Gio.DBus.session,
            BUS_NAME,
            Gio.BusNameOwnerFlags.REPLACE,
            null,
            null
        );
    }

    disable() {
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = null;
        }
        if (this._dbus) {
            this._dbus.unexport();
            this._dbus = null;
        }
        this._windowIds = null;
        this._windowsById = null;
    }

    ListWindows() {
        const tracker = Shell.WindowTracker.get_default();
        this._pruneWindows();
        const windows = global.get_window_actors()
            .map(actor => actor.meta_window)
            .filter(window => window && !window.skip_taskbar)
            .map(window => {
                const app = tracker.get_window_app(window);
                const workspace = window.get_workspace();
                const id = this._trackWindow(window);
                return {
                    id,
                    app_id: app ? app.get_id() : '',
                    title: window.get_title() || '',
                    workspace: workspace ? workspace.index() : null,
                    wm_class: window.get_wm_class() || '',
                    focused: window.has_focus(),
                };
            });
        return JSON.stringify(windows);
    }

    FocusWindow(windowId) {
        const window = this._windowsById.get(windowId);
        if (!window || !this._currentWindows().has(window)) {
            return JSON.stringify({status: 'not_found', window_id: windowId});
        }

        const workspace = window.get_workspace();
        if (workspace) {
            workspace.activate(global.get_current_time());
        }
        window.activate(global.get_current_time());
        return JSON.stringify({status: 'focused', window_id: windowId});
    }

    MinimizeWindow(windowId) {
        const window = this._lookupWindow(windowId);
        if (!window) {
            return JSON.stringify({status: 'not_found', window_id: windowId});
        }
        window.minimize();
        return JSON.stringify({status: 'minimized', window_id: windowId});
    }

    MaximizeWindow(windowId) {
        const window = this._lookupWindow(windowId);
        if (!window) {
            return JSON.stringify({status: 'not_found', window_id: windowId});
        }
        window.maximize(Meta.MaximizeFlags.BOTH);
        return JSON.stringify({status: 'maximized', window_id: windowId});
    }

    CloseWindow(windowId) {
        const window = this._lookupWindow(windowId);
        if (!window) {
            return JSON.stringify({status: 'not_found', window_id: windowId});
        }
        window.delete(global.get_current_time());
        return JSON.stringify({status: 'closed', window_id: windowId});
    }

    SetClipboard(text) {
        if (!text) {
            return JSON.stringify({status: 'failed', error: 'clipboard text must not be empty'});
        }
        St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
        return JSON.stringify({status: 'written', adapter: 'gnome-shell'});
    }

    GetClipboardAsync(_params, invocation) {
        St.Clipboard.get_default().get_text(
            St.ClipboardType.CLIPBOARD,
            (_clipboard, text) => invocation.return_value(
                new GLib.Variant('(s)', [text ?? ''])
            )
        );
    }

    _trackWindow(window) {
        let id = this._windowIds.get(window);
        if (!id) {
            id = String(this._nextWindowId++);
            this._windowIds.set(window, id);
        }
        this._windowsById.set(id, window);
        return id;
    }

    _pruneWindows() {
        const current = this._currentWindows();
        for (const [id, window] of this._windowsById.entries()) {
            if (!window || !current.has(window)) {
                this._windowsById.delete(id);
            }
        }
    }

    _lookupWindow(windowId) {
        const window = this._windowsById.get(windowId);
        if (!window || !this._currentWindows().has(window)) {
            return null;
        }
        return window;
    }

    _currentWindows() {
        return new Set(global.get_window_actors()
            .map(actor => actor.meta_window)
            .filter(window => window));
    }
}
