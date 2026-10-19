using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;
using Microsoft.CodeAnalysis.CSharp.Syntax;

var root = Path.GetFullPath(args[0]);
var messages = new List<object>();
var diagnostics = new List<string>();
var files = new List<string>();
foreach (var directory in new[] { "dev/CSLModernMapRenderer/winui" })
foreach (var file in Directory.EnumerateFiles(Path.Combine(root, directory), "*.cs", SearchOption.AllDirectories))
{
    var path = Path.GetRelativePath(root, file).Replace('\\', '/');
    if (path.Split('/').Any(part => part is "bin" or "obj")) continue;
    files.Add(path);
    var source = File.ReadAllText(file);
    var tree = CSharpSyntaxTree.ParseText(source);
    diagnostics.AddRange(tree.GetDiagnostics().Where(d => d.Severity == DiagnosticSeverity.Error).Select(d => path + ": " + d));
    var syntax = tree.GetRoot();
    foreach (var node in syntax.DescendantNodes().Where(n => n is InterpolatedStringExpressionSyntax || n is LiteralExpressionSyntax literal && literal.IsKind(SyntaxKind.StringLiteralExpression)))
    {
        if (node.Ancestors().Any(n => n is InterpolatedStringExpressionSyntax or AttributeSyntax)) continue;
        var method = node.Ancestors().OfType<MemberDeclarationSyntax>().FirstOrDefault(n => n is MethodDeclarationSyntax or PropertyDeclarationSyntax or FieldDeclarationSyntax);
        var member = method switch
        {
            MethodDeclarationSyntax m => m.Identifier.Text,
            PropertyDeclarationSyntax p => p.Identifier.Text,
            FieldDeclarationSyntax f => f.Declaration.Variables.First().Identifier.Text,
            _ => ""
        };
        var statement = node.Ancestors().FirstOrDefault(n => n is StatementSyntax or EqualsValueClauseSyntax or ArrowExpressionClauseSyntax);
        var context = statement?.ToString() ?? node.Parent?.ToString() ?? "";
        var log = node.Ancestors().OfType<InvocationExpressionSyntax>().Any(call => Regex.IsMatch(call.Expression.ToString(), @"(?:log|Log|Logging|Debug|Trace|Console)\.(?:Error|Warn|Warning|Info|Message|WriteLine|Write|Log|LogError|LogWarning)$"));
        var text = node is LiteralExpressionSyntax value ? value.Token.ValueText : string.Concat(((InterpolatedStringExpressionSyntax)node).Contents.Select(c => c is InterpolatedStringTextSyntax t ? t.TextToken.ValueText : c.ToString()));
        var english = "";
        if (node.Parent is ArgumentSyntax argument && argument.Parent?.Parent is InvocationExpressionSyntax bilingual && bilingual.ArgumentList.Arguments.Count >= 2)
        {
            var list = bilingual.ArgumentList.Arguments;
            if (list[0] == argument && list[1].Expression is LiteralExpressionSyntax en) english = en.Token.ValueText;
        }
        if (node.Parent is ArgumentSyntax tupleArg && tupleArg.Parent is TupleExpressionSyntax tuple && tuple.Arguments.Count == 2 && tuple.Arguments[0] == tupleArg && tuple.Arguments[1].Expression is LiteralExpressionSyntax tupleEn)
            english = tupleEn.Token.ValueText;
        var conditional = node.Ancestors().OfType<ConditionalExpressionSyntax>().FirstOrDefault();
        if (conditional is not null && conditional.WhenTrue.Span.Contains(node.Span))
        {
            var en = conditional.WhenFalse.DescendantNodesAndSelf().OfType<LiteralExpressionSyntax>().FirstOrDefault(n => n.IsKind(SyntaxKind.StringLiteralExpression));
            if (en is not null) english = en.Token.ValueText;
        }
        else if (conditional is not null && conditional.WhenFalse.Span.Contains(node.Span))
        {
            var en = conditional.WhenTrue.DescendantNodesAndSelf().OfType<LiteralExpressionSyntax>().FirstOrDefault(n => n.IsKind(SyntaxKind.StringLiteralExpression));
            if (en is not null) english = en.Token.ValueText;
        }
        messages.Add(new { path, line = tree.GetLineSpan(node.Span).StartLinePosition.Line + 1, start = node.SpanStart,
            text, english, member, context = context.Length > 900 ? context[..900] : context, log,
            interpolated = node is InterpolatedStringExpressionSyntax });
    }
}
File.WriteAllText(args[1], JsonSerializer.Serialize(new { messages, diagnostics, files }, new JsonSerializerOptions { WriteIndented = true }));
Console.WriteLine($"C# syntax files: {files.Count}; literals: {messages.Count}; parse errors: {diagnostics.Count}");
