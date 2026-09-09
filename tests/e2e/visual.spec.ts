import { expect, test, type Page, type TestInfo } from "@playwright/test";

import {
  isMobileProject,
  mockAuthenticated,
  openAuthenticatedWorkspace,
} from "./fixtures";

async function workspaceGeometry(page: Page) {
  return page.evaluate(() => {
    const root = document.documentElement;
    const transcript = document.querySelector<HTMLElement>(
      ".workspace-main__content, .workspace-mobile__content",
    );
    const productBar = document.querySelector<HTMLElement>(".app-product-bar");
    const workspace = document.querySelector<HTMLElement>(
      ".workspace-grid, .workspace-mobile",
    );
    const composer = document.querySelector<HTMLElement>(".composer");
    if (!transcript) throw new Error("workspace transcript is missing");
    if (!productBar || !workspace || !composer) {
      throw new Error("workspace chrome is missing");
    }
    const productBarRect = productBar.getBoundingClientRect();
    const workspaceRect = workspace.getBoundingClientRect();
    const composerRect = composer.getBoundingClientRect();
    const workspaceStyle = getComputedStyle(workspace);
    return {
      documentWidth: [root.scrollWidth, root.clientWidth],
      documentHeight: [root.scrollHeight, root.clientHeight],
      transcriptHeight: [transcript.scrollHeight, transcript.clientHeight],
      chrome: {
        productBarBottom: productBarRect.bottom,
        workspaceTop: workspaceRect.top,
        workspaceBottom: workspaceRect.bottom,
        composerBottom: composerRect.bottom,
        viewportBottom: root.clientHeight,
        animationName: workspaceStyle.animationName,
        reducedMotion: matchMedia("(prefers-reduced-motion: reduce)").matches,
      },
    };
  });
}

async function expectNoOverflowOrClipping(page: Page): Promise<void> {
  const [result, geometry] = await Promise.all([
    page.evaluate(() => {
      const interactive = Array.from(
        document.querySelectorAll<HTMLElement>(
          "button, a, input, textarea, [role='tab'], h1, h2, h3",
        ),
      ).filter((element) => {
        const style = getComputedStyle(element);
        const rect = element.getBoundingClientRect();
        return (
          style.visibility !== "hidden" &&
          style.display !== "none" &&
          rect.width > 0 &&
          rect.height > 0
        );
      });
      return {
        horizontalScroll:
          document.documentElement.scrollWidth > window.innerWidth + 1,
        outsideViewport: interactive
          .filter((element) => {
            const rect = element.getBoundingClientRect();
            return rect.left < -1 || rect.right > window.innerWidth + 1;
          })
          .map(
            (element) =>
              element.getAttribute("aria-label") ?? element.textContent.trim(),
          ),
        clipped: interactive
          .filter(
            (element) =>
              (element.tagName === "BUTTON" ||
                element.getAttribute("role") === "tab") &&
              (element.scrollWidth > element.clientWidth + 1 ||
                element.scrollHeight > element.clientHeight + 1),
          )
          .map(
            (element) =>
              element.getAttribute("aria-label") ?? element.textContent.trim(),
          ),
      };
    }),
    workspaceGeometry(page),
  ]);
  expect(result).toEqual({
    horizontalScroll: false,
    outsideViewport: [],
    clipped: [],
  });
  expect(geometry.documentWidth[0]).toBe(geometry.documentWidth[1]);
  expect(geometry.documentHeight[0]).toBe(geometry.documentHeight[1]);
  expect(geometry.chrome.workspaceTop).toBe(geometry.chrome.productBarBottom);
  expect(geometry.chrome.workspaceBottom).toBe(geometry.chrome.viewportBottom);
  expect(geometry.chrome.composerBottom).toBe(geometry.chrome.viewportBottom);
  expect(geometry.chrome.reducedMotion).toBe(true);
  expect(geometry.chrome.animationName).toBe("none");
}

async function runChat(page: Page): Promise<void> {
  await page
    .getByLabel("Analysis request")
    .fill("Explain the current variance briefly");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("The answer is grounded and complete."),
  ).toBeVisible();
}

test("empty workspace visual integrity", async ({
  page,
}, testInfo: TestInfo) => {
  await openAuthenticatedWorkspace(page, testInfo);
  await expectNoOverflowOrClipping(page);
  const geometry = await workspaceGeometry(page);
  expect(geometry.transcriptHeight[0]).toBe(geometry.transcriptHeight[1]);
  await expect(page).toHaveScreenshot("empty-workspace.png", {
    fullPage: true,
  });
});

test("streaming response visual integrity", async ({ page }, testInfo) => {
  await openAuthenticatedWorkspace(page, testInfo);
  if (isMobileProject(testInfo)) {
    await page.getByRole("button", { name: "Analyses" }).click();
    await expectNoOverflowOrClipping(page);
    await expect(page).toHaveScreenshot("mobile-drawer.png", {
      fullPage: true,
    });
    return;
  }

  await runChat(page);
  await expectNoOverflowOrClipping(page);
  await expect(page).toHaveScreenshot("streaming-response.png", {
    fullPage: true,
  });
});

test("degraded stream visual integrity", async ({ page }, testInfo) => {
  test.skip(isMobileProject(testInfo), "desktop stream surface only");
  await mockAuthenticated(page);
  await page.route("**/api/tasks/task_visual/events", async (route) => {
    await route.abort("connectionfailed");
  });
  await page.goto("/?task=task_visual");
  await expect(
    page.getByText("Stream gap; using canonical state"),
  ).toBeVisible();
  await expectNoOverflowOrClipping(page);
  await expect(page).toHaveScreenshot("degraded-stream.png", {
    fullPage: true,
  });
});

test("long conversation owns the only scroll surface", async ({
  page,
}, testInfo) => {
  await openAuthenticatedWorkspace(page, testInfo);
  await page.locator(".workspace-empty").evaluate((element) => {
    element.remove();
  });
  await page.locator(".conversation").evaluate((conversation) => {
    for (let index = 1; index <= 32; index += 1) {
      const article = document.createElement("article");
      article.className = "conversation__message conversation__message--user";
      article.textContent = `Question ${String(index)}: compare regional revenue`;
      conversation.append(article);
    }
  });

  const geometry = await workspaceGeometry(page);
  expect(geometry.documentWidth[0]).toBe(geometry.documentWidth[1]);
  expect(geometry.documentHeight[0]).toBe(geometry.documentHeight[1]);
  expect(geometry.chrome.workspaceTop).toBe(geometry.chrome.productBarBottom);
  expect(geometry.chrome.workspaceBottom).toBe(geometry.chrome.viewportBottom);
  expect(geometry.chrome.composerBottom).toBe(geometry.chrome.viewportBottom);
  expect(geometry.chrome.reducedMotion).toBe(true);
  expect(geometry.chrome.animationName).toBe("none");
  expect(geometry.transcriptHeight[0]).toBeGreaterThan(
    geometry.transcriptHeight[1],
  );

  const transcript = page.locator(
    ".workspace-main__content, .workspace-mobile__content",
  );
  await transcript.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect(
    page.getByText("Question 32: compare regional revenue"),
  ).toBeInViewport();
});
