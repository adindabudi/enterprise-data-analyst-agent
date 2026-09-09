import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { DomUtils, parseDocument } from "htmlparser2";
import inliner from "web-resource-inliner";

const [input = "dist/index.html", output = "bundle.html"] =
  process.argv.slice(2);
const inputPath = path.resolve(input);
const outputPath = path.resolve(output);
const fileContent = fs.readFileSync(inputPath, "utf8");
const scriptTypes = DomUtils.getElementsByTagName(
  "script",
  parseDocument(fileContent, { decodeEntities: false }).children,
).map((script) => script.attribs.type);

inliner.html(
  {
    fileContent,
    relativeTo: path.dirname(inputPath),
    strict: true,
    scripts: true,
    links: true,
    images: true,
    svgs: true,
    requestResource: (_request, callback) =>
      callback(new Error("remote resources are forbidden")),
  },
  (error, result) => {
    if (error) {
      process.stderr.write(`${error.message}\n`);
      process.exitCode = 1;
      return;
    }
    const document = parseDocument(result, { decodeEntities: false });
    const scripts = DomUtils.getElementsByTagName("script", document.children);
    if (scripts.length !== scriptTypes.length) {
      process.stderr.write("script structure changed during inlining\n");
      process.exitCode = 1;
      return;
    }
    scripts.forEach((script, index) => {
      const type = scriptTypes[index];
      if (type !== undefined) script.attribs.type = type;
    });
    fs.writeFileSync(
      outputPath,
      DomUtils.getOuterHTML(document, { decodeEntities: false }),
      "utf8",
    );
  },
);
