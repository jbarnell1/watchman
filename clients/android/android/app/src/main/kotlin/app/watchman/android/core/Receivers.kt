package app.watchman.android.core

import android.app.admin.DeviceAdminReceiver
import android.app.admin.DevicePolicyManager
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (AppState.get(context).pairing != null) WatchmanService.start(context)
    }
}

/** Device admin: stops a casual uninstall and reports its own removal first (plan, "Tamper"). */
class WatchmanDeviceAdmin : DeviceAdminReceiver() {
    companion object {
        fun component(context: Context) = ComponentName(context, WatchmanDeviceAdmin::class.java)
        fun isActive(context: Context): Boolean =
            (context.getSystemService(Context.DEVICE_POLICY_SERVICE) as DevicePolicyManager).isAdminActive(component(context))
    }

    override fun onDisableRequested(context: Context, intent: Intent): CharSequence {
        report(context, "device_admin_disable_requested")
        return context.getString(app.watchman.android.R.string.device_admin_description)
    }

    override fun onDisabled(context: Context, intent: Intent) {
        report(context, "device_admin_disabled")
        AppState.get(context).onChanged?.invoke()
    }

    private fun report(context: Context, reason: String) {
        val state = AppState.get(context)
        if (state.pairing == null) return
        state.record(WatchmanEvent(type = "tamper", occurredAt = Iso.now(), app = "android", action = "none",
            detail = mapOf("reason" to reason)), null)
    }
}
