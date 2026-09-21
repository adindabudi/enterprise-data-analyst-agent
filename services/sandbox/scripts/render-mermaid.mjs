#!/usr/bin/env node

import { readFile, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import path from "node:path";
import { parseArgs } from "node:util";

const HELP = `Usage: mmdc -i input.mmd -o output.svg [options]

Options:
  -i, --input <path>
  -o, --output <path>
  -p, --puppeteerConfigFile <path>
  -c, --configFile <path>
  -b, --backgroundColor <color>
  -w, --width <pixels>
  -H, --height <pixels>
  -s, --scale <factor>
  -h, --help
`;

const { values } = parseArgs({
  options: {
    input: { type: "string", short: "i" },
    output: { type: "string", short: "o" },
    puppeteerConfigFile: { type: "string", short: "p" },
    configFile: { type: "string", short: "c" },
    backgroundColor: { type: "string", short: "b", default: "white" },
    width: { type: "string", short: "w", default: "800" },
    height: { type: "string", short: "H", default: "600" },
    scale: { type: "string", short: "s", default: "1" },
    help: { type: "boolean", short: "h" },
  },
  strict: true,
});

if (values.help) {
  globalThis.process.stdout.write(HELP);
  globalThis.process.exit(0);
}

if (!values.input || !values.output) {
  globalThis.process.stderr.write(HELP);
  globalThis.process.exit(2);
}

const width = positiveNumber(values.width, "width");
const height = positiveNumber(values.height, "height");
const scale = positiveNumber(values.scale, "scale");
const format = path.extname(values.output).slice(1).toLowerCase();
if (!new Set(["svg", "png", "pdf"]).has(format)) {
  throw new Error("output extension must be .svg, .png, or .pdf");
}

const source = await readFile(values.input, "utf8");
const mermaidConfig = values.configFile
  ? await readJson(values.configFile)
  : {};
const puppeteerConfig = values.puppeteerConfigFile
  ? await readJson(values.puppeteerConfigFile)
  : {};
const { default: puppeteer } = await import("puppeteer");
const require = createRequire(import.meta.url);
const mermaidEntry = require.resolve("mermaid");
const mermaidBundle = path.join(path.dirname(mermaidEntry), "mermaid.min.js");
const browser = await puppeteer.launch({
  executablePath: globalThis.process.env.PUPPETEER_EXECUTABLE_PATH,
  ...puppeteerConfig,
});

try {
  const page = await browser.newPage();
  await page.setViewport({ width, height, deviceScaleFactor: scale });
  await page.setContent('<main id="diagram"></main>');
  await page.addScriptTag({ path: mermaidBundle });
  const svg = await page.evaluate(
    async ({ config, definition, background }) => {
      if (!globalThis.CSS.supports("color", background))
        throw new Error("backgroundColor must be a valid CSS color");
      globalThis.document.body.style.margin = "0";
      globalThis.document.body.style.backgroundColor = background;
      globalThis.mermaid.initialize({
        startOnLoad: false,
        ...config,
        securityLevel: "strict",
      });
      const rendered = await globalThis.mermaid.render(
        "eda-mermaid-diagram",
        definition,
      );
      globalThis.document.querySelector("#diagram").innerHTML = rendered.svg;
      const element = globalThis.document.querySelector("#diagram svg");
      if (!element) throw new Error("Mermaid did not render an SVG element");
      element.style.backgroundColor = background;
      await globalThis.document.fonts.ready;
      return element.outerHTML;
    },
    {
      config: mermaidConfig,
      definition: source,
      background: values.backgroundColor,
    },
  );

  if (format === "svg") {
    await writeFile(values.output, svg, "utf8");
  } else if (format === "png") {
    const element = await page.$("#diagram svg");
    if (!element) throw new Error("Mermaid did not render an SVG element");
    await element.screenshot({
      path: values.output,
      omitBackground: values.backgroundColor === "transparent",
    });
  } else {
    await page.pdf({
      path: values.output,
      printBackground: true,
      width,
      height,
    });
  }
} finally {
  await browser.close();
}

function positiveNumber(value, name) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0)
    throw new Error(`${name} must be a positive number`);
  return parsed;
}

async function readJson(filePath) {
  const value = JSON.parse(await readFile(filePath, "utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error(`${filePath} must contain an object`);
  return value;
}
