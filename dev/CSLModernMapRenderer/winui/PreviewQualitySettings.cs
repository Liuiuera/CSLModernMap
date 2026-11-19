using System.Text.Json;
using System.Text.Json.Serialization;

namespace CSLModernMapWinUI;

internal sealed record PreviewQualitySettings([property: JsonRequired] double PixelScale = 1,
    [property: JsonRequired] string LineDetail = "standard", [property: JsonRequired] string PolygonDetail = "standard")
{
    internal static readonly JsonSerializerOptions JsonOptions = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };

    internal string Preset => this == new PreviewQualitySettings(.75) ? "Smooth" :
        this == new PreviewQualitySettings() ? "Standard" :
        this == new PreviewQualitySettings(1.25, "fine", "fine") ? "Fine" : "Custom";

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
            PolygonDetail is not ("standard" or "fine"))
            throw new InvalidDataException("Invalid preview quality settings");
    }
}
