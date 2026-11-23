using System.Text.Json;
using System.Text.Json.Serialization;

namespace CSLModernMapWinUI;

internal sealed record PreviewQualitySettings([property: JsonRequired] double PixelScale = 1,
    [property: JsonRequired] string LineDetail = "standard", [property: JsonRequired] string PolygonDetail = "standard",
    int TextPercent = 100, int LinePercent = 100, int IconPercent = 100)
{
    internal static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    internal string Preset => (PixelScale, LineDetail, PolygonDetail) switch
    {
        (.75, "standard", "standard") => "Smooth",
        (1, "standard", "standard") => "Standard",
        (1.25, "fine", "fine") => "Fine",
        _ => "Custom",
    };

    internal PreviewQualitySettings WithPreset(string preset) =>
        FromPreset(preset) with { TextPercent = TextPercent, LinePercent = LinePercent, IconPercent = IconPercent };

    internal static PreviewQualitySettings FromPreset(string preset) => preset switch
    {
        "Smooth" => new(.75),
        "Standard" => new(),
        "Fine" => new(1.25, "fine", "fine"),
        _ => throw new ArgumentException("Unknown preview quality preset", nameof(preset)),
    };

    internal void Validate()
    {
        if (PixelScale is not (.75 or 1 or 1.25) || LineDetail is not ("standard" or "fine") ||
            PolygonDetail is not ("standard" or "fine") ||
            TextPercent is < 50 or > 200 || LinePercent is < 50 or > 200 || IconPercent is < 50 or > 200)
            throw new InvalidDataException("Invalid preview quality settings");
    }
}
