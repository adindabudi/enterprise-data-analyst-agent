import {
  expect,
  test,
  type Locator,
  type Page,
  type TestInfo,
} from "@playwright/test";

import type {
  PublishedArtifact,
  SourceQuery,
} from "../../apps/web/src/api/analysis";
import { isMobileProject, openAuthenticatedWorkspace } from "./fixtures";

test.beforeEach(async ({ baseURL, context }) => {
  expect(
    baseURL,
    "Mocked details tests must never target live acceptance",
  ).toBe("http://127.0.0.1:4173");
  await context.route(/^https?:\/\//, async (route) => {
    if (new URL(route.request().url()).origin !== baseURL) {
      await route.abort("blockedbyclient");
      return;
    }
    await route.fallback();
  });
});

const taskId = "task_e2e_12345678";
const sessionId = "ses_e2e_12345678";
const longName =
  "lamna_healthcare_regional_capacity_and_utilization_2026_quarter_3_board_review_with_reconciled_facility_totals";

function artifact(
  name: string,
  kind: string,
  displayName: string,
): PublishedArtifact {
  return {
    artifactId: `artifact-${name}_12345678`,
    version: 2,
    kind,
    sha256: "a".repeat(64),
    displayName,
    sizeBytes: 8192,
  };
}

const inputArtifact = artifact("input", "input", `${longName}_input.csv`);
const htmlArtifact = artifact("html", "html", `${longName}_dashboard.html`);
const docxArtifact = artifact("docx", "docx", `${longName}_report.docx`);
const workbookArtifact = artifact("workbook", "xlsx", `${longName}_data.xlsx`);
const firstRows = artifact("rooms", "dataset", "rooms-query-rows.json");
const secondRows = artifact("patients", "dataset", "patients-query-rows.json");
const scriptArtifact = artifact("script", "script", "diagnostic-analysis.py");
const manifestArtifact = artifact("manifest", "manifest", "run-manifest.json");
const deliverables = [htmlArtifact, docxArtifact, workbookArtifact];
const excludedOutputs = [
  inputArtifact,
  firstRows,
  secondRows,
  scriptArtifact,
  manifestArtifact,
];
const artifacts = [...excludedOutputs, ...deliverables];
const firstQuery: SourceQuery = {
  artifactId: firstRows.artifactId,
  version: firstRows.version,
  sha256: firstRows.sha256,
  displayName: firstRows.displayName,
  query: "MATCH (room:rooms) RETURN count(room)",
  querySha256: "b".repeat(64),
  rowCount: 12,
  sourceAlias: "lamna-healthcare",
  messageId: "msg_rooms_12345678",
  executedAt: "2026-09-06T01:00:00Z",
};
const secondQuery: SourceQuery = {
  ...firstQuery,
  artifactId: secondRows.artifactId,
  version: secondRows.version,
  displayName: secondRows.displayName,
  query: "MATCH (patient:patients) RETURN count(patient)",
  querySha256: "c".repeat(64),
  rowCount: 37,
  messageId: "msg_patients_12345678",
};

function contentPath(item: PublishedArtifact): string {
  return `/api/tasks/${taskId}/artifacts/${item.artifactId}/versions/${String(item.version)}/content`;
}

async function openSection(page: Page, name: string): Promise<Locator> {
  const toggle = page.getByRole("button", {
    name: new RegExp(`^${name}(?:\\s|$)`),
  });
  if ((await toggle.getAttribute("aria-expanded")) !== "true")
    await toggle.click();
  return page.getByRole("region", { name, exact: true });
}

async function mockTaskDetails(
  page: Page,
  sourceQueries: SourceQuery[] = [firstQuery, secondQuery],
): Promise<void> {
  await page.route(`**/api/tasks/${taskId}/artifacts`, async (route) => {
    await route.fulfill({ json: { artifacts } });
  });
  await page.route(`**/api/tasks/${taskId}/provenance`, async (route) => {
    await route.fulfill({ json: { sourceQueries, artifacts } });
  });
  await page.route(`**/api/sessions/${sessionId}/todos`, async (route) => {
    await route.fulfill({ json: { items: [] } });
  });
}

async function expectContained(locator: Locator): Promise<void> {
  const metrics = await locator.evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return {
      selector: element.getAttribute("class"),
      left: bounds.left,
      top: bounds.top,
      right: bounds.right,
      bottom: bounds.bottom,
      width: bounds.width,
      height: bounds.height,
      viewportWidth: window.innerWidth,
      viewportHeight: window.innerHeight,
      scrollWidth: element.scrollWidth,
      clientWidth: element.clientWidth,
    };
  });
  const evidence = JSON.stringify(metrics);
  expect.soft(metrics.width, evidence).toBeGreaterThan(0);
  expect.soft(metrics.height, evidence).toBeGreaterThan(0);
  expect.soft(metrics.left, evidence).toBeGreaterThanOrEqual(-1);
  expect.soft(metrics.top, evidence).toBeGreaterThanOrEqual(-1);
  expect
    .soft(metrics.right, evidence)
    .toBeLessThanOrEqual(metrics.viewportWidth + 1);
  expect
    .soft(metrics.bottom, evidence)
    .toBeLessThanOrEqual(metrics.viewportHeight + 1);
  expect
    .soft(metrics.scrollWidth, evidence)
    .toBeLessThanOrEqual(metrics.clientWidth + 1);
}

async function captureDesktop(page: Page, testInfo: TestInfo): Promise<void> {
  await page.evaluate(() => document.fonts.ready.then(() => undefined));
  const path = testInfo.outputPath(
    testInfo.project.name === "minimum-desktop"
      ? "eda-details-minimum.png"
      : testInfo.project.name === "desktop"
        ? "eda-details-desktop.png"
        : "details.png",
  );
  await page.screenshot({ path, animations: "disabled" });
  await testInfo.attach("details-outputs", { path, contentType: "image/png" });
}

test("final Outputs excludes input, raw query data and diagnostics", async ({
  page,
}, testInfo) => {
  test.skip(isMobileProject(testInfo), "The details panel is desktop-only");
  await mockTaskDetails(page);
  await openAuthenticatedWorkspace(page, testInfo, `?task=${taskId}`);

  const inputs = await openSection(page, "Inputs");
  await expect(
    inputs.getByText(inputArtifact.displayName, { exact: true }),
  ).toBeVisible();
  const inputLink = inputs.getByRole("link", {
    name: `Download ${inputArtifact.displayName}`,
    exact: true,
  });
  await expect(inputLink).toHaveAttribute("href", contentPath(inputArtifact));
  await expect(inputLink).toHaveAttribute(
    "download",
    inputArtifact.displayName,
  );
  await expect(inputs.getByRole("link")).toHaveCount(1);

  const outputs = await openSection(page, "Outputs");
  for (const item of deliverables) {
    await expect(
      outputs.getByText(item.displayName, { exact: true }),
    ).toBeVisible();
    await expect(
      outputs.locator(`a[href="${contentPath(item)}"]`),
    ).toHaveAttribute("download", item.displayName);
  }
  for (const item of excludedOutputs) {
    await expect(
      outputs.getByText(item.displayName, { exact: true }),
    ).toHaveCount(0);
  }
  await expect(outputs.getByRole("link", { name: /^Download / })).toHaveCount(
    deliverables.length,
  );
});

test("query log associates task provenance by question messageId", async ({
  page,
}, testInfo) => {
  test.skip(isMobileProject(testInfo), "The details panel is desktop-only");
  await mockTaskDetails(page, [secondQuery, firstQuery]);
  await openAuthenticatedWorkspace(page, testInfo);
  let messageCount = 0;
  let taskRequests = 0;
  await page.route(`**/api/sessions/${sessionId}/messages`, async (route) => {
    messageCount += 1;
    await route.fulfill({
      status: 201,
      json: {
        messageId:
          messageCount === 1 ? firstQuery.messageId : secondQuery.messageId,
      },
    });
  });
  await page.route(`**/api/sessions/${sessionId}/tasks`, async (route) => {
    taskRequests += 1;
    await route.fulfill({
      status: 202,
      json: { taskId, sessionId, status: "planning" },
    });
  });

  const composer = page.getByRole("textbox", { name: "Analysis request" });
  await composer.fill("How many rooms?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("The answer is grounded and complete.", { exact: true }),
  ).toBeVisible();
  await composer.fill("How many patients?");
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(page).toHaveURL(
    new RegExp(`[?]session=${sessionId}&task=${taskId}$`),
  );
  const provenance = await openSection(page, "Provenance");
  const rooms = provenance.getByRole("group", {
    name: "How many rooms?",
    exact: true,
  });
  const patients = provenance.getByRole("group", {
    name: "How many patients?",
    exact: true,
  });
  await expect(rooms.locator(".query-text")).toHaveText([firstQuery.query]);
  await expect(patients.locator(".query-text")).toHaveText([secondQuery.query]);
  await expect(rooms.getByText(secondQuery.query, { exact: true })).toHaveCount(
    0,
  );
  await expect(
    patients.getByText(firstQuery.query, { exact: true }),
  ).toHaveCount(0);
  await expect(provenance.getByRole("group")).toHaveCount(2);
  await expect(provenance.locator(".details-artifact")).toHaveCount(2);
  await expect(provenance.getByRole("tree")).toHaveCount(0);
  for (const rows of [firstRows, secondRows]) {
    const link = provenance.getByRole("link", {
      name: rows.displayName,
      exact: true,
    });
    await expect(link).toHaveAttribute("href", contentPath(rows));
    await expect(link).toHaveAttribute("download", rows.displayName);
  }
  await expect(provenance.getByRole("link")).toHaveCount(2);
  for (const item of [
    inputArtifact,
    scriptArtifact,
    manifestArtifact,
    ...deliverables,
  ]) {
    await expect(
      provenance.getByText(item.displayName, { exact: true }),
    ).toHaveCount(0);
  }
  expect(messageCount).toBe(2);
  expect(taskRequests).toBe(2);

  await page
    .getByRole("button", { name: "New chat", exact: true })
    .first()
    .click();
  await openSection(page, "Provenance");
  await expect(
    provenance.getByText("No queries yet.", { exact: true }),
  ).toBeVisible();
  await expect(provenance.locator(".query-text")).toHaveCount(0);
});

test("resumed query log does not invent question associations", async ({
  page,
}, testInfo) => {
  test.skip(isMobileProject(testInfo), "The details panel is desktop-only");
  const legacyQuery: SourceQuery = {
    ...firstQuery,
    artifactId: "artifact-legacy_12345678",
    messageId: null,
    query: "MATCH (hospital:hospitals) RETURN count(hospital)",
  };
  await mockTaskDetails(page, [secondQuery, legacyQuery, firstQuery]);
  await openAuthenticatedWorkspace(page, testInfo, `?task=${taskId}`);
  await expect(
    page.getByText("The answer is grounded and complete.", { exact: true }),
  ).toBeVisible();
  const provenance = await openSection(page, "Provenance");
  const groups = provenance.getByRole("group", {
    name: "Question text unavailable",
    exact: true,
  });
  await expect(groups).toHaveCount(3);
  await expect(provenance.getByRole("group")).toHaveCount(3);
  await expect(provenance.locator(".query-text")).toHaveText([
    secondQuery.query,
    legacyQuery.query,
    firstQuery.query,
  ]);
  await expect(
    provenance.getByRole("group", { name: "How many rooms?" }),
  ).toHaveCount(0);
  await expect(
    provenance.getByRole("group", { name: "How many patients?" }),
  ).toHaveCount(0);
  await expect(provenance.getByText(/msg_(rooms|patients|reply)_/)).toHaveCount(
    0,
  );
  await expect(groups.nth(1).getByRole("link")).toHaveCount(0);
});

test("collapsible sections keep keyboard access and scrolling inside the workspace", async ({
  page,
}, testInfo) => {
  test.skip(isMobileProject(testInfo), "The details panel is desktop-only");
  const history = Array.from({ length: 8 }, (_, index): SourceQuery => ({
    ...firstQuery,
    artifactId: `artifact-history_${String(index)}_12345678`,
    query: `MATCH (room:rooms) RETURN room.capacity AS ${longName}_${String(index)}`,
  }));
  await mockTaskDetails(page, [firstQuery, ...history, secondQuery]);
  await openAuthenticatedWorkspace(page, testInfo, `?task=${taskId}`);
  for (const name of ["Tasks", "Inputs"]) {
    await page
      .getByRole("button", { name: new RegExp(`^${name}(?:\\s|$)`) })
      .click();
  }
  const outputs = await openSection(page, "Outputs");
  await expect(outputs.locator(".workspace-file")).toHaveCount(
    deliverables.length,
  );
  await captureDesktop(page, testInfo);
  for (const selector of [
    ".workspace-grid",
    ".workspace-main",
    ".workspace-main__content",
    ".workspace-details",
    ".workspace-sections",
    ".composer",
  ]) {
    await expectContained(page.locator(selector));
  }
  const filenames = await outputs
    .locator(".workspace-file")
    .evaluateAll((items) =>
      items.map((item) => {
        const filename = item.querySelector(".workspace-file__name > span");
        if (!filename) throw new Error("Artifact filename is missing");
        const range = document.createRange();
        range.selectNodeContents(filename);
        const bounds = item.getBoundingClientRect();
        return {
          selector: ".workspace-file__name > span:first-child",
          filename: filename.textContent,
          left: bounds.left,
          right: bounds.right,
          textBounds: Array.from(range.getClientRects(), (rect) => ({
            left: rect.left,
            right: rect.right,
          })),
        };
      }),
    );
  for (const filename of filenames) {
    for (const bounds of filename.textBounds) {
      expect
        .soft(bounds.left, JSON.stringify(filename))
        .toBeGreaterThanOrEqual(filename.left - 1);
      expect
        .soft(bounds.right, JSON.stringify(filename))
        .toBeLessThanOrEqual(filename.right + 1);
    }
  }
  await outputs.locator(".workspace-file").last().scrollIntoViewIfNeeded();
  await expect(outputs.locator(".workspace-file").last()).toBeInViewport({
    ratio: 1,
  });

  const sections = page.locator(".workspace-sections");
  const sectionNames = ["Tasks", "Inputs", "Outputs", "Provenance"];
  await expect(page.getByRole("tablist")).toHaveCount(0);
  await expect(sections.locator(".fui-AccordionHeader__button")).toHaveCount(
    sectionNames.length,
  );
  for (const name of sectionNames) {
    const header = sections.getByRole("button", {
      name: new RegExp(`^${name}(?:\\s|$)`),
    });
    if ((await header.getAttribute("aria-expanded")) === "true")
      await header.click();
    await expect(header).toHaveAttribute("aria-expanded", "false");
  }
  await sections.getByRole("button", { name: "Tasks", exact: true }).focus();
  for (const name of sectionNames.slice(1)) {
    await page.keyboard.press("Tab");
    await expect(
      sections.getByRole("button", { name: new RegExp(`^${name}(?:\\s|$)`) }),
    ).toBeFocused();
  }
  await page.keyboard.press("Enter");
  const provenanceHeader = sections.getByRole("button", {
    name: "Provenance",
    exact: true,
  });
  await expect(provenanceHeader).toHaveAttribute("aria-expanded", "true");
  const provenance = page.getByRole("region", { name: "Provenance" });
  await expect(provenance.locator(".query-text")).toHaveCount(10);
  await expect(sections.getByRole("region")).toHaveCount(1);
  await page.screenshot({
    path: testInfo.outputPath("provenance.png"),
    animations: "disabled",
  });

  await expect(provenanceHeader).toBeInViewport({ ratio: 1 });
  await expectContained(sections);
  const scrolling = await sections.evaluate((element) => ({
    selector: ".workspace-sections",
    scrollHeight: element.scrollHeight,
    clientHeight: element.clientHeight,
    overflowY: getComputedStyle(element).overflowY,
  }));
  expect(scrolling.scrollHeight, JSON.stringify(scrolling)).toBeGreaterThan(
    scrolling.clientHeight,
  );
  expect(["auto", "scroll"], JSON.stringify(scrolling)).toContain(
    scrolling.overflowY,
  );
  await sections.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await expect(provenance.locator(".details-artifact").last()).toBeInViewport({
    ratio: 1,
  });
  const documentSize = await page.evaluate(() => ({
    selector: "html",
    width: document.documentElement.scrollWidth,
    viewportWidth: window.innerWidth,
    scrollY: window.scrollY,
  }));
  expect(documentSize.width, JSON.stringify(documentSize)).toBeLessThanOrEqual(
    documentSize.viewportWidth,
  );
  expect(documentSize.scrollY).toBe(0);
});

test("HTML preview runs in an opaque sandbox and closing restores trigger focus", async ({
  page,
}, testInfo) => {
  test.skip(isMobileProject(testInfo), "The details panel is desktop-only");
  await mockTaskDetails(page);
  let previewReads = 0;
  await page.route(`**${contentPath(htmlArtifact)}`, async (route) => {
    previewReads += 1;
    expect(route.request().method()).toBe("GET");
    expect(route.request().headers()["accept"]).toBe("text/html");
    await route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><body>
        <h1>Local capacity report</h1><p id="render-status">Waiting for script</p>
        <button id="totals">Show totals</button><p id="total-value"></p>
        <script>
          document.getElementById("render-status").textContent = "Local script rendered";
          document.getElementById("totals").onclick = () => {
            document.getElementById("total-value").textContent = "37 patients";
          };
        </script></body></html>`,
    });
  });
  await openAuthenticatedWorkspace(page, testInfo, `?task=${taskId}`);
  await openSection(page, "Outputs");
  const trigger = page.getByRole("button", {
    name: `Preview ${htmlArtifact.displayName}`,
    exact: true,
  });
  await expect(trigger).toBeVisible();
  expect(previewReads).toBe(0);
  await trigger.click();
  const dialog = page.getByRole("dialog", {
    name: `Preview ${htmlArtifact.displayName}`,
    exact: true,
  });
  await expect(dialog).toBeVisible();
  const iframe = dialog.locator('iframe[title="Artifact preview"]');
  await expect(iframe).toHaveAttribute("sandbox", "allow-scripts");
  await expect(iframe).not.toHaveAttribute("sandbox", /allow-same-origin/);
  await expect(iframe).toHaveAttribute("referrerpolicy", "no-referrer");
  await expect(iframe).toHaveAttribute("srcdoc", /connect-src 'none'/);
  const preview = page.frameLocator('iframe[title="Artifact preview"]');
  await expect(
    preview.getByRole("heading", { name: "Local capacity report" }),
  ).toBeVisible();
  await expect(
    preview.getByText("Local script rendered", { exact: true }),
  ).toBeVisible();
  await preview.getByRole("button", { name: "Show totals" }).click();
  await expect(preview.getByText("37 patients", { exact: true })).toBeVisible();
  const parentAccess = await preview.locator("body").evaluate(() => {
    try {
      return window.parent.document.title;
    } catch (error) {
      return error instanceof DOMException ? error.name : "Unexpected error";
    }
  });
  expect(parentAccess).toBe("SecurityError");
  await page.screenshot({
    path: testInfo.outputPath("html-preview.png"),
    animations: "disabled",
  });
  await dialog.getByRole("button", { name: "Close preview" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
  expect(previewReads).toBe(1);
  await trigger.click();
  await expect(iframe).toBeVisible();
  await dialog.getByRole("button", { name: "Close preview" }).focus();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

async function openMobileWorkspace(
  page: Page,
  testInfo: TestInfo,
): Promise<void> {
  await mockTaskDetails(page);
  await openAuthenticatedWorkspace(page, testInfo);
  await page.setViewportSize({
    width:
      testInfo.project.name === "minimum-desktop" ||
      testInfo.project.name === "narrow-mobile"
        ? 320
        : 390,
    height: 844,
  });
  await expect(
    page.getByRole("button", { name: "Analyses", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Analyses", exact: true }),
  ).toHaveCount(0);
}

test("mobile upload gates Send on mocked scanning, scan_failed and clean states", async ({
  page,
}, testInfo) => {
  await openMobileWorkspace(page, testInfo);
  const uploadId = "upl_details_12345678";
  const uploadName = `${longName}_mobile.csv`;
  let scanState = "scanning";
  let statusReads = 0;
  let uploadBody = "";
  const taskBodies: unknown[] = [];
  await page.route(`**/api/sessions/${sessionId}/uploads`, async (route) => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().headers()["content-type"]).toContain(
      "multipart/form-data",
    );
    uploadBody = route.request().postDataBuffer()?.toString("utf8") ?? "";
    await route.fulfill({
      status: 202,
      json: { uploadId, displayName: uploadName, state: "scanning" },
    });
  });
  await page.route(
    `**/api/sessions/${sessionId}/uploads/${uploadId}`,
    async (route) => {
      statusReads += 1;
      await route.fulfill({
        json: { uploadId, displayName: uploadName, state: scanState },
      });
    },
  );
  await page.route(`**/api/sessions/${sessionId}/tasks`, async (route) => {
    taskBodies.push(route.request().postDataJSON());
    await route.fulfill({
      status: 202,
      json: { taskId, sessionId, status: "planning" },
    });
  });
  const composer = page.getByRole("textbox", { name: "Analysis request" });
  await composer.fill("Analyze this local input");
  await page.getByRole("button", { name: "Add context" }).click();
  const chooserReady = page.waitForEvent("filechooser");
  await page.getByRole("menuitem", { name: "Attach file" }).click();
  const chooser = await chooserReady;
  await chooser.setFiles({
    name: uploadName,
    mimeType: "text/csv",
    buffer: Buffer.from("facility,capacity\nwest,42\n"),
  });

  const status = page.getByRole("status", {
    name: "Input upload",
    exact: true,
  });
  await expect(
    status.getByText("Checking the file for malware…", { exact: true }),
  ).toBeVisible();
  await expect(status.getByText(uploadName, { exact: true })).toBeVisible();
  expect(uploadBody).toContain(`name="upload"; filename="${uploadName}"`);
  expect(uploadBody).toContain("facility,capacity\nwest,42\n");
  await expect(
    page.getByRole("button", { name: "Wait for the file scan to finish" }),
  ).toBeDisabled();
  await composer.press("Enter");
  expect(taskBodies).toHaveLength(0);
  expect(statusReads).toBe(0);

  scanState = "scan_failed";
  await expect(status.getByText("Scan failed", { exact: true })).toBeVisible({
    timeout: 3_000,
  });
  await expect(
    page.getByRole("button", { name: "Remove the file to send without it" }),
  ).toBeDisabled();
  scanState = "clean";
  await page.getByRole("button", { name: "Refresh upload status" }).click();
  await expect(status.getByText("Clean", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Send message" }),
  ).toBeEnabled();
  expect(statusReads).toBeGreaterThanOrEqual(2);
  expect(taskBodies).toHaveLength(0);
  await expectContained(page.locator(".composer"));
  await expectContained(page.locator(".input-upload__status"));
  await page.screenshot({
    path: testInfo.outputPath("mobile-upload.png"),
    animations: "disabled",
  });
  await page.getByRole("button", { name: "Send message" }).click();
  await expect.poll(() => taskBodies.length).toBe(1);
  expect(taskBodies[0]).toMatchObject({ inputUploadIds: [uploadId] });
  await expect(status).toHaveCount(0);
});

test("mobile rejected mock upload can be removed before sending a task", async ({
  page,
}, testInfo) => {
  await openMobileWorkspace(page, testInfo);
  let taskRequests = 0;
  await page.route(`**/api/sessions/${sessionId}/uploads`, async (route) => {
    await route.fulfill({
      status: 202,
      json: {
        uploadId: "upl_rejected_12345678",
        displayName: "rejected-local.csv",
        state: "rejected",
      },
    });
  });
  await page.route(`**/api/sessions/${sessionId}/tasks`, async (route) => {
    taskRequests += 1;
    await route.fulfill({
      status: 202,
      json: { taskId, sessionId, status: "planning" },
    });
  });
  await page.getByLabel("Choose analysis input").setInputFiles({
    name: "rejected-local.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("facility,capacity\nwest,42\n"),
  });
  const status = page.getByRole("status", {
    name: "Input upload",
    exact: true,
  });
  await expect(status.getByText("Rejected", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Refresh upload status" }),
  ).toHaveCount(0);
  const composer = page.getByRole("textbox", { name: "Analysis request" });
  await composer.fill("Question without the rejected file");
  await expect(
    page.getByRole("button", { name: "Remove the file to send without it" }),
  ).toBeDisabled();
  await composer.press("Enter");
  expect(taskRequests).toBe(0);
  await page.getByRole("button", { name: "Remove attachment" }).click();
  await expect(status).toHaveCount(0);
  await expect(
    page.getByText("rejected-local.csv", { exact: true }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("The answer is grounded and complete.", { exact: true }),
  ).toBeVisible();
  expect(taskRequests).toBe(1);
});
