package app.watchman.android.core

/**
 * Matches an app switcher tile's label (what the launcher reads out, "YouTube" or "YouTube, 2 minutes
 * ago") against the names of apps that had covered pictures. Case-insensitive, by the start of the label.
 */
object SwitcherMatch {
    fun matches(label: String, coveredApps: Collection<String>): Boolean {
        val l = label.trim().lowercase()
        if (l.isEmpty()) return false
        return coveredApps.any { name ->
            val n = name.trim().lowercase()
            n.isNotEmpty() && (l == n || l.startsWith("$n,") || l.startsWith("$n ") || l.startsWith(n))
        }
    }
}
