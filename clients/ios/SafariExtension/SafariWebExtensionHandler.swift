import SafariServices
import os.log

/// The native side of the Safari web extension. The extension itself is the shared WebExtension
/// from clients/extension (dist/firefox, copied in as resources); nothing native is needed yet.
final class SafariWebExtensionHandler: NSObject, NSExtensionRequestHandling {
    func beginRequest(with context: NSExtensionContext) {
        let response = NSExtensionItem()
        response.userInfo = [SFExtensionMessageKey: ["ok": true]]
        context.completeRequest(returningItems: [response], completionHandler: nil)
    }
}
