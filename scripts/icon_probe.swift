#!/usr/bin/env swift
// Audit the glyphs a terminal font really provides before they reach the prompt.
//
// usage:
//   scripts/icon_probe.swift check  <font> <text>...
//   scripts/icon_probe.swift line   <font> <text>...
//   scripts/icon_probe.swift config <font> <shell-file>...
//
// <font> is a PostScript name such as ComicShannsMonoNFM. Text arguments accept
// \uXXXX, \u{XXXXX} and \UXXXXXXXX escapes, so an icon can be written without
// pasting a private-use character. `config` reads the shell appearance files and
// audits every glyph they can display; Powerlevel10k's icons.zsh is read only in
// the nerdfont-complete preset branch, which is the preset this package ships.
//
// Why it matters: iTerm2 lays every character out in a single cell. A glyph that
// is missing from the configured font is drawn by a CoreText fallback font at a
// width that no longer matches the cell, and a glyph whose ink is wider than its
// cell overlaps the character after it. Both cases are reported here, and
// --strict turns them into a non-zero exit for scripting.

import Foundation
import CoreText

let usage = """
usage: scripts/icon_probe.swift check  <font> <text>...
       scripts/icon_probe.swift line   <font> <text>...
       scripts/icon_probe.swift config <font> <shell-file>...
"""

let overWideLimit = 1.10
let cellSource = "M"

func die(_ message: String) -> Never {
    FileHandle.standardError.write(Data(("icon_probe: \(message)\n" + usage + "\n").utf8))
    exit(2)
}

func loadFont(_ name: String) -> CTFont {
    let font = CTFontCreateWithName(name as CFString, 100, nil)
    let resolved = CTFontCopyPostScriptName(font) as String
    if resolved.caseInsensitiveCompare(name) != .orderedSame {
        die("font \(name) is not installed (PostScript name resolved to \(resolved))")
    }
    return font
}

/// Expands the backslash escapes the shell files use, so icons can be typed.
func decodeEscapes(_ text: String) -> String {
    var result = ""
    var index = text.startIndex
    while index < text.endIndex {
        let character = text[index]
        let next = text.index(after: index)
        if character == "\\", next < text.endIndex, text[next] == "u" || text[next] == "U" {
            var cursor = text.index(after: next)
            var hex = ""
            if cursor < text.endIndex, text[cursor] == "{" {
                cursor = text.index(after: cursor)
                while cursor < text.endIndex, text[cursor] != "}" {
                    hex.append(text[cursor])
                    cursor = text.index(after: cursor)
                }
                if cursor < text.endIndex { cursor = text.index(after: cursor) }
            } else {
                let limit = text[next] == "U" ? 8 : 4
                while cursor < text.endIndex, hex.count < limit, text[cursor].isHexDigit {
                    hex.append(text[cursor])
                    cursor = text.index(after: cursor)
                }
            }
            if let value = UInt32(hex, radix: 16), let scalar = Unicode.Scalar(value) {
                result.append(Character(scalar))
                index = cursor
                continue
            }
        }
        result.append(character)
        index = next
    }
    return result
}

func cellWidth(_ font: CTFont) -> CGFloat {
    var glyphs = [CGGlyph](repeating: 0, count: cellSource.utf16.count)
    guard CTFontGetGlyphsForCharacters(font, Array(cellSource.utf16), &glyphs, glyphs.count) else {
        die("font \(CTFontCopyPostScriptName(font) as String) has no glyph for \(cellSource)")
    }
    var advances = [CGSize](repeating: .zero, count: glyphs.count)
    CTFontGetAdvancesForGlyphs(font, .horizontal, glyphs, &advances, glyphs.count)
    return advances.map { $0.width }.reduce(0, +)
}

struct Metrics {
    var present: Bool
    var advance: Double?
    var ink: Double?

    var flag: String {
        guard present else { return "fallback" }
        guard let ink else { return "unmeasured" }
        return ink > overWideLimit ? "over-wide" : "ok"
    }
    var isDefect: Bool { flag != "ok" && flag != "unmeasured" }
}

func metrics(_ font: CTFont, _ scalar: Unicode.Scalar, _ charset: CharacterSet, _ cell: CGFloat) -> Metrics {
    guard charset.contains(scalar) else { return Metrics(present: false, advance: nil, ink: nil) }
    guard scalar.value <= 0xFFFF else { return Metrics(present: true, advance: nil, ink: nil) }
    var glyphs = [CGGlyph](repeating: 0, count: 1)
    guard CTFontGetGlyphsForCharacters(font, Array(String(Character(scalar)).utf16), &glyphs, 1) else {
        return Metrics(present: true, advance: nil, ink: nil)
    }
    var advances = [CGSize](repeating: .zero, count: 1)
    CTFontGetAdvancesForGlyphs(font, .horizontal, glyphs, &advances, 1)
    var ink = CGRect.zero
    CTFontGetBoundingRectsForGlyphs(font, .horizontal, &glyphs, &ink, 1)
    return Metrics(
        present: true,
        advance: Double(advances[0].width / cell),
        ink: Double(ink.width / cell))
}

func pad(_ text: String, _ width: Int, right: Bool = false) -> String {
    guard text.count < width else { return text }
    let fill = String(repeating: " ", count: width - text.count)
    return right ? fill + text : text + fill
}

func number(_ value: Double?) -> String {
    guard let value else { return "-" }
    return String(format: "%.2f", value)
}

/// Runs CoreText layout on a whole string and reports which fonts were used.
func reportLine(_ font: CTFont, _ cell: CGFloat, _ raw: String) -> [Bool] {
    let text = decodeEscapes(raw)
    let attributed = NSAttributedString(string: text, attributes: [
        NSAttributedString.Key(kCTFontAttributeName as String): font,
    ])
    let line = CTLineCreateWithAttributedString(attributed)
    let total = Double(CTLineGetTypographicBounds(line, nil, nil, nil) / cell)
    var defects: [Bool] = []
    print("\(raw)  cells=\(number(total))")
    for run in CTLineGetGlyphRuns(line) as! [CTRun] {
        let attributes = CTRunGetAttributes(run) as NSDictionary
        let used = attributes[kCTFontAttributeName as String] as! CTFont
        let width = Double(CTRunGetTypographicBounds(run, CFRange(location: 0, length: 0), nil, nil, nil) / cell)
        let name = CTFontCopyPostScriptName(used) as String
        let fallback = name.caseInsensitiveCompare(CTFontCopyPostScriptName(font) as String) != .orderedSame
        defects.append(fallback || width > overWideLimit)
        print("    \(fallback ? "fallback" : "       ") \(pad(name, 28)) x\(CTRunGetGlyphCount(run)) \(number(width))")
    }
    return defects
}

/// Every glyph a shell file can display, with the line it came from.
func collect(_ path: String, _ charset: CharacterSet) -> [(scalar: Unicode.Scalar, source: String)] {
    guard let text = try? String(contentsOfFile: path, encoding: .utf8) else {
        die("cannot read \(path)")
    }
    var lines = text.split(separator: "\n", omittingEmptySubsequences: false).map(String.init)
    var offset = 0
    if (path as NSString).lastPathComponent == "icons.zsh" {
        // Only the preset branch this package ships; the others define different glyphs.
        guard let start = lines.firstIndex(where: { $0.contains("'nerdfont-complete'") }),
              let end = lines[(start + 1)...].firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == ";;" })
        else { die("\(path) has no nerdfont-complete preset branch") }
        lines = Array(lines[start..<end])
        offset = start
    }
    let escape = try! NSRegularExpression(pattern: "\\\\[uU](?:\\{([0-9A-Fa-f]{2,6})\\}|([0-9A-Fa-f]{4,8}))")
    var found: [UInt32: String] = [:]
    for (index, line) in lines.enumerated() {
        let location = "\((path as NSString).lastPathComponent):\(offset + index + 1)"
        let range = NSRange(line.startIndex..., in: line)
        for match in escape.matches(in: line, range: range) {
            let hex = (1...2).compactMap { group -> String? in
                guard let captured = Range(match.range(at: group), in: line) else { return nil }
                return String(line[captured])
            }.first ?? ""
            if let value = UInt32(hex, radix: 16), let scalar = Unicode.Scalar(value) {
                found[value] = found[value] ?? location
            }
        }
        if line.trimmingCharacters(in: .whitespaces).hasPrefix("#") { continue }
        for character in line {
            guard let scalar = character.unicodeScalars.first, character.unicodeScalars.count == 1 else { continue }
            let value = scalar.value
            guard value > 0x7F else { continue }
            let isCjk = (0x3040...0x30FF).contains(value) || (0x4E00...0x9FFF).contains(value)
                || (0x3400...0x4DBF).contains(value) || (0x3000...0x303F).contains(value)
                || (0xFF00...0xFFEF).contains(value) || (0xFE00...0xFE0F).contains(value)
            if isCjk { continue }
            found[value] = found[value] ?? location
        }
    }
    return found.sorted { $0.key < $1.key }.map { (Unicode.Scalar($0.key)!, $0.value) }
}

let arguments = CommandLine.arguments
guard arguments.count >= 4 else { die("missing arguments") }
let mode = arguments[1]
let font = loadFont(arguments[2])
let inputs = Array(arguments[3...])
let strict = inputs.contains("--strict")
let payload = inputs.filter { $0 != "--strict" }
guard !payload.isEmpty else { die("missing input") }
let cell = cellWidth(font)
let charset = CTFontCopyCharacterSet(font) as CharacterSet

if mode == "line" {
    var defects = false
    for raw in payload { defects = reportLine(font, cell, raw).contains(true) || defects }
    exit(strict && defects ? 1 : 0)
}

guard mode == "check" || mode == "config" else { die("unknown mode \(mode)") }

var rows: [(scalar: Unicode.Scalar, source: String)] = []
if mode == "check" {
    var seen = Set<UInt32>()
    for raw in payload {
        for scalar in decodeEscapes(raw).unicodeScalars where seen.insert(scalar.value).inserted {
            rows.append((scalar, "argument"))
        }
    }
} else {
    for path in payload { rows.append(contentsOf: collect(path, charset)) }
}

print("# font=\(CTFontCopyPostScriptName(font) as String) cell=\(String(format: "%.1f", cell))pt mode=\(mode)")
var counts: [String: Int] = [:]
for row in rows {
    let result = metrics(font, row.scalar, charset, cell)
    counts[result.flag, default: 0] += 1
    print(pad(String(format: "U+%04X", row.scalar.value), 9)
        + pad(String(describing: Character(row.scalar)).debugDescription, 14)
        + pad(row.source, 22)
        + pad(result.flag, 11)
        + "adv=\(pad(number(result.advance), 5)) ink=\(number(result.ink))")
    if result.isDefect {
        print("    -> \(result.present ? "glyph ink is wider than its cell" : "glyph missing from the font, drawn by a fallback font")")
    }
}
let defects = (counts["fallback"] ?? 0) + (counts["over-wide"] ?? 0)
print("summary: \(rows.count) glyphs, ok=\(counts["ok"] ?? 0) fallback=\(counts["fallback"] ?? 0) over-wide=\(counts["over-wide"] ?? 0)")
exit(strict && defects > 0 ? 1 : 0)
