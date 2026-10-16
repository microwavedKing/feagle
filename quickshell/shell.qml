// Example Quickshell entrypoint that loads the feagle launcher.
//
//   quickshell -p /path/to/feagle/quickshell
//
// Then open it from a compositor hotkey with:
//   qs ipc call feagle-launcher toggle
import Quickshell

ShellRoot {
    FeagleLauncher {}
}
