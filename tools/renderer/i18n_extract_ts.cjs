const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(process.argv[2]);
const ts = require(path.join(root, 'dev/CSLModernMapRenderer/viewer/node_modules/typescript'));
const dir = path.join(root, 'dev/CSLModernMapRenderer/viewer/src');
const messages = [], files = [], diagnostics = [];
const inputs = fs.readdirSync(dir).filter(name => /\.ts$/.test(name)).sort().map(name => ({path: 'dev/CSLModernMapRenderer/viewer/src/' + name, text: fs.readFileSync(path.join(dir, name), 'utf8'), line: 1}));
if (process.argv[4]) inputs.push(...JSON.parse(fs.readFileSync(process.argv[4], 'utf8')));
for (const input of inputs) {
  const relative = input.path;
  files.push(relative);
  const source = ts.createSourceFile(relative, input.text, ts.ScriptTarget.Latest, true, input.path.endsWith('.py') ? ts.ScriptKind.JS : ts.ScriptKind.TS);
  for (const diagnostic of source.parseDiagnostics) diagnostics.push(relative + ': ' + ts.flattenDiagnosticMessageText(diagnostic.messageText, '\n'));
  function visit(node) {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateExpression(node)) {
      let text = node.text;
      if (ts.isTemplateExpression(node)) text = node.head.text + node.templateSpans.map(span => '{' + span.expression.getText(source) + '}' + span.literal.text).join('');
      let ancestor = node, member = '', context = node.parent.getText(source);
      while (ancestor.parent) {
        ancestor = ancestor.parent;
        if (ts.isFunctionDeclaration(ancestor) || ts.isMethodDeclaration(ancestor)) { member = ancestor.name?.getText(source) || ''; break; }
      }
      let statement = node;
      while (statement.parent && !ts.isStatement(statement)) statement = statement.parent;
      if (ts.isStatement(statement)) context = statement.getText(source);
      messages.push({ path: relative, line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + input.line,
        text, member, context: context.slice(0, 900), interpolated: ts.isTemplateExpression(node),
        log: /console\.(log|warn|error|info)\(/.test(context) });
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}
fs.writeFileSync(process.argv[3], JSON.stringify({ messages, files, diagnostics }, null, 2));
console.log(`TypeScript syntax files: ${files.length}; literals: ${messages.length}; parse errors: ${diagnostics.length}`);
