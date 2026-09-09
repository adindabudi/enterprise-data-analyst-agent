import { mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

import { compileFromFile } from "json-schema-to-typescript";
import prettier from "prettier";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const schemaDir = path.join(root, "packages/contracts/schema");
const outputDir = path.join(
  root,
  "packages/contracts/typescript/src/generated",
);

await mkdir(outputDir, { recursive: true });
const schemaFiles = (await readdir(schemaDir))
  .filter((name) => name.endsWith(".schema.json"))
  .sort();

for (const schemaFile of schemaFiles) {
  const schemaPath = path.join(schemaDir, schemaFile);
  const source = await readFile(schemaPath, "utf8");
  const formatted = await prettier.format(source, { parser: "json" });
  await writeFile(schemaPath, formatted, "utf8");
}

for (const schemaFile of schemaFiles) {
  const outputName = schemaFile.replace(".schema.json", ".d.ts");
  const source = await compileFromFile(path.join(schemaDir, schemaFile), {
    bannerComment:
      "/* Generated from canonical Pydantic JSON Schema. Do not edit. */",
    style: { singleQuote: false },
    unknownAny: false,
  });
  const formatted = await prettier.format(source, { parser: "typescript" });
  await writeFile(path.join(outputDir, outputName), formatted, "utf8");
}

process.stdout.write(
  `Generated ${schemaFiles.length} TypeScript contract files\n`,
);
