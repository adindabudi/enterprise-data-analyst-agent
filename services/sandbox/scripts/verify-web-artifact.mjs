import path from "node:path";
import process from "node:process";
import { clearTimeout, setTimeout } from "node:timers";
import { pathToFileURL, URL } from "node:url";
import puppeteer from "puppeteer";

let browser;
let firstFailure;
function recordFailure(error) {
  if (firstFailure !== undefined) return;
  firstFailure = (error instanceof Error ? error.message : String(error)).slice(
    0,
    1500,
  );
}

const deadline = setTimeout(() => {
  const browserProcess = browser?.process();
  if (browserProcess?.pid) {
    try {
      process.kill(-browserProcess.pid, "SIGKILL");
    } catch {
      browserProcess.kill("SIGKILL");
    }
  }
  process.stderr.write(
    "web artifact render validation failed: browser exceeded its execution deadline\n",
  );
  process.exit(1);
}, 45000);
deadline.unref();

try {
  const input = process.argv[2];
  if (!input) throw new Error("web artifact input is required");
  const documentUrl = pathToFileURL(path.resolve(input)).href;
  browser = await puppeteer.launch({
    executablePath:
      process.env.PUPPETEER_EXECUTABLE_PATH ?? "/usr/bin/chromium",
    headless: true,
    pipe: true,
    timeout: 15000,
    protocolTimeout: 15000,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800 });
  let blockedResource = false;
  page.on("pageerror", recordFailure);
  await page.exposeFunction("__edaReportCspViolation", () => {
    recordFailure("web artifact violated its content security policy");
  });
  await page.evaluateOnNewDocument(() => {
    globalThis.addEventListener("securitypolicyviolation", () => {
      void globalThis.__edaReportCspViolation();
    });
  });
  await page.setRequestInterception(true);
  page.on("request", (request) => {
    const url = new URL(request.url());
    const allowed =
      url.protocol === "data:" ||
      url.protocol === "blob:" ||
      (url.href === documentUrl &&
        request.isNavigationRequest() &&
        request.frame() === page.mainFrame());
    if (!allowed) blockedResource = true;
    void (
      allowed ? request.continue() : request.abort("blockedbyclient")
    ).catch(recordFailure);
  });
  await page.goto(documentUrl, { waitUntil: "load", timeout: 15000 });
  if (firstFailure !== undefined) throw new Error(firstFailure);
  try {
    await page.waitForFunction(
      () => {
        const root = globalThis.document.getElementById("root");
        if (!root) return false;
        for (let element = root; element; element = element.parentElement) {
          const style = globalThis.getComputedStyle(element);
          if (
            style.display === "none" ||
            style.visibility === "hidden" ||
            Number(style.opacity) === 0
          )
            return false;
        }
        if (root.innerText.trim()) return true;
        return [...root.querySelectorAll("canvas,img,svg,video")].some(
          (element) => {
            const bounds = element.getBoundingClientRect();
            return bounds.width > 0 && bounds.height > 0;
          },
        );
      },
      { timeout: 5000 },
    );
  } catch {
    throw new Error(firstFailure ?? "web artifact rendered no visible content");
  }
  if (firstFailure !== undefined) throw new Error(firstFailure);
  if (blockedResource)
    throw new Error("web artifact requested a non-embedded resource");
} catch (error) {
  recordFailure(error);
} finally {
  try {
    await browser?.close();
  } catch (error) {
    recordFailure(error);
  } finally {
    clearTimeout(deadline);
  }
}
if (firstFailure !== undefined) {
  process.stderr.write(
    `web artifact render validation failed: ${firstFailure}\n`,
  );
  process.exitCode = 1;
}
