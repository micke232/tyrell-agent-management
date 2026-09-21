// Preserve every pasteboard item's data without printing or saving clipboard contents.
import AppKit
import Foundation
let board = NSPasteboard.general
let saved = (board.pasteboardItems ?? []).map { item in
    item.types.compactMap { type -> (NSPasteboard.PasteboardType, Data)? in
        guard let data = item.data(forType: type) else { return nil }
        return (type, data)
    }
}
let child = Process()
child.executableURL = URL(fileURLWithPath: CommandLine.arguments[1])
child.arguments = Array(CommandLine.arguments.dropFirst(2))
var status: Int32 = 1
do {
    try child.run()
    child.waitUntilExit()
    status = child.terminationStatus
} catch {
    fputs("Could not launch clipboard verification\n", stderr)
}
board.clearContents()
let restored = saved.map { entries -> NSPasteboardItem in
    let item = NSPasteboardItem()
    for (type, data) in entries { item.setData(data, forType: type) }
    return item
}
if !restored.isEmpty { board.writeObjects(restored) }
let actualItems = board.pasteboardItems ?? []
let matches = actualItems.count == saved.count && zip(actualItems, saved).allSatisfy { item, entries in
    entries.allSatisfy { type, data in item.data(forType: type) == data }
}
if !matches {
    fputs("Clipboard restoration did not match original pasteboard data\n", stderr)
    status = 1
} else {
    print("PASS original clipboard items restored without writing them to disk")
}
exit(status)
