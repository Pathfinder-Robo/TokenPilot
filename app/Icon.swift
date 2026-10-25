import AppKit
let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
func render(size: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    let context = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.current = context
    context.cgContext.scaleBy(x: CGFloat(size)/1024, y: CGFloat(size)/1024)
    let tile = NSBezierPath(roundedRect: NSRect(x: 54,y: 54,width: 916,height: 916), xRadius: 210,yRadius: 210)
    NSGradient(starting: NSColor(calibratedRed:0.16,green:0.20,blue:0.24,alpha:1),ending:NSColor(calibratedRed:0.055,green:0.065,blue:0.10,alpha:1))!.draw(in: tile, angle: -70)
    NSColor.white.withAlphaComponent(0.16).setStroke();tile.lineWidth=3;tile.stroke()
    let mint=NSColor(calibratedRed:0.46,green:0.95,blue:0.77,alpha:1)
    let purple=NSColor(calibratedRed:0.72,green:0.62,blue:1,alpha:1)
    let ring=NSBezierPath(ovalIn:NSRect(x:213,y:213,width:598,height:598))
    ring.lineWidth=56;NSColor.white.withAlphaComponent(0.055).setStroke();ring.stroke()
    func arc(_ start: CGFloat,_ end: CGFloat,_ color:NSColor) {
        let path=NSBezierPath();path.appendArc(withCenter:NSPoint(x:512,y:512),radius:299,startAngle:start,endAngle:end)
        path.lineWidth=56;path.lineCapStyle = .round;color.setStroke();path.stroke()
    }
    arc(44,193,mint);arc(224,373,purple)
    // A split compass needle combines the two provider colors into one direction.
    let left=NSBezierPath();left.move(to:NSPoint(x:512,y:748));left.line(to:NSPoint(x:350,y:364));left.line(to:NSPoint(x:512,y:446));left.close()
    NSGradient(starting:mint,ending:NSColor(calibratedRed:0.36,green:0.77,blue:0.69,alpha:1))!.draw(in:left,angle:-90)
    let right=NSBezierPath();right.move(to:NSPoint(x:512,y:748));right.line(to:NSPoint(x:674,y:364));right.line(to:NSPoint(x:512,y:446));right.close()
    NSGradient(starting:NSColor(calibratedRed:0.87,green:0.83,blue:1,alpha:1),ending:purple)!.draw(in:right,angle:-90)
    let seam=NSBezierPath();seam.move(to:NSPoint(x:512,y:740));seam.line(to:NSPoint(x:512,y:451));seam.lineWidth=2;NSColor.white.withAlphaComponent(0.22).setStroke();seam.stroke()
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using:.png,properties:[:])!
}
for (name,size) in [("icon_16x16",16),("icon_16x16@2x",32),("icon_32x32",32),("icon_32x32@2x",64),("icon_128x128",128),("icon_128x128@2x",256),("icon_256x256",256),("icon_256x256@2x",512),("icon_512x512",512),("icon_512x512@2x",1024)] {
    try render(size:size).write(to:root.appendingPathComponent(name+".png"))
}
