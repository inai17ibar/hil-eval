import { test, expect, type APIRequestContext } from "@playwright/test";
async function seed(request: APIRequestContext, extra = {}) {
  const response = await request.post("/v1/items", {
    data: {
      project: "e2e",
      input: "question",
      output: { answer: "old", count: 1 },
      ...extra,
    },
  });
  expect(response.status()).toBe(201);
  return response.json();
}
test("approve from a direct item link", async ({ page, request }) => {
  const item = await seed(request);
  await page.goto(`/items/${item.id}`);
  await expect(page.locator("article")).toContainText(item.id);
  await page.getByRole("button", { name: "Approve", exact: false }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/v1/items/${item.id}`)).json()).status,
    )
    .toBe("approved");
});
test("choose an option with a reason", async ({ page, request }) => {
  await request.post("/v1/projects/e2e/reason-codes", {
    data: { code: "quality", label: "Quality" },
  });
  const item = await seed(request, { options: ["red", "blue"] });
  await page.goto(`/items/${item.id}`);
  await page.getByLabel("Reason codes").selectOption("quality");
  await page.getByLabel("Reason text", { exact: true }).fill("Best match");
  await page
    .getByRole("button", { name: 'Choose "blue"', exact: true })
    .click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/v1/items/${item.id}`)).json()).reviews[0]
          ?.choice,
    )
    .toBe("blue");
  const saved = await (await request.get(`/v1/items/${item.id}`)).json();
  expect(saved.reviews[0].reason_codes).toEqual(["quality"]);
  expect(saved.reviews[0].reason_text).toBe("Best match");
});
test("edit structured output and inspect diff", async ({ page, request }) => {
  const item = await seed(request);
  await page.goto(`/items/${item.id}`);
  await page.getByRole("button", { name: "Edit e", exact: true }).click();
  await page.getByLabel("answer", { exact: true }).fill("new");
  await expect(page.locator('[aria-label="Output diff"] del')).toContainText(
    '"old",',
  );
  await expect(page.locator('[aria-label="Output diff"] ins')).toContainText(
    '"new",',
  );
  await page.getByRole("button", { name: "Save edit", exact: true }).click();
  await expect
    .poll(
      async () =>
        (await (await request.get(`/v1/items/${item.id}`)).json()).reviews[0]
          ?.corrected_output,
    )
    .toEqual({ answer: "new", count: 1 });
});
test("selection updates the URL and browser history restores the selection", async ({
  page,
  request,
}) => {
  const first = await seed(request, { fn_name: "first-link" });
  const second = await seed(request, { fn_name: "second-link" });
  await page.goto(`/items/${first.id}`);
  await page.getByRole("button", { name: /second-link/ }).click();
  await expect(page).toHaveURL(new RegExp(`/items/${second.id}$`));
  await page.goBack();
  await expect(page.locator("article")).toContainText(first.id);
});

test("seed audio has a playable WAV source", async ({ page, request }) => {
  await page.goto("/items/seed-example");
  const audio = page.locator("article audio");
  await expect(audio).toBeVisible();
  await expect(audio).toHaveAttribute("preload", "metadata");
  const src = await audio.getAttribute("src");
  expect(src).toBeTruthy();
  const response = await request.get(src!);
  expect(response.status()).toBe(200);
  expect(response.headers()["content-type"]).toBe("audio/wav");
  await expect
    .poll(() => audio.evaluate((el) => (el as HTMLAudioElement).readyState))
    .toBeGreaterThanOrEqual(1);
  await expect
    .poll(() => audio.evaluate((el) => (el as HTMLAudioElement).currentTime))
    .toBeGreaterThanOrEqual(0.25);
});

const LABEL_FIELDS = [
  { name: "family", kind: "enum", choices: ["drums", "keys", "bass"] },
  {
    name: "leaf",
    kind: "enum",
    depends_on: "family",
    choices: {
      drums: ["kick", "snare"],
      keys: ["piano", "rhodes"],
      bass: ["808"],
    },
  },
  { name: "type", kind: "enum", choices: ["loop", "one_shot"] },
  {
    name: "bpm",
    kind: "number",
    min: 40,
    max: 240,
    integer: true,
    multipliers: [0.5, 2],
  },
  { name: "key", kind: "text" },
];
const labelItem = (request: APIRequestContext) =>
  seed(request, {
    project: "labels-e2e",
    output: {
      family: "drums",
      leaf: "kick",
      type: "loop",
      bpm: 100,
      key: null,
    },
    fields: LABEL_FIELDS,
  });
const latestReview = async (request: APIRequestContext, id: string) =>
  (await (await request.get(`/v1/items/${id}`)).json()).reviews.at(-1);

test("confirm all labels as proposed with Enter", async ({ page, request }) => {
  const item = await labelItem(request);
  await page.goto(`/items/${item.id}`);
  await expect(page.getByText("No changes")).toBeVisible();
  await page.locator("h1").first().click();
  await page.keyboard.press("Enter");
  await expect
    .poll(() => latestReview(request, item.id))
    .toMatchObject({
      action: "approve",
      field_diff: {},
      fields: {
        family: "drums",
        leaf: "kick",
        type: "loop",
        bpm: 100,
        key: null,
      },
    });
});

test("correct only the wrong fields with chips and keyboard", async ({
  page,
  request,
}) => {
  const item = await labelItem(request);
  await page.goto(`/items/${item.id}`);
  const family = page.getByRole("radiogroup", { name: "family" });
  await family.getByRole("radio", { name: "keys" }).click();
  // the hierarchical child no longer fits: it is cleared and highlighted
  await expect(page.locator(".label-row.changed")).toHaveCount(2);
  await expect(page.getByRole("radio", { name: "kick" })).toHaveCount(0);
  await page.getByRole("radio", { name: "rhodes" }).click();
  await page.locator("h1").first().click();
  await page.keyboard.press("4"); // jump to bpm
  await expect(page.getByRole("textbox", { name: "bpm" })).toBeFocused();
  await page.getByRole("button", { name: "×0.5" }).click();
  await expect(page.locator(".label-row.changed")).toHaveCount(3);
  await expect(
    page.getByRole("button", { name: /Save 3 corrections/ }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Reset family" }).click();
  // resetting the parent also restores the child that no longer fits
  await expect(page.locator(".label-row.changed")).toHaveCount(1);
  await expect(page.getByRole("radio", { name: "kick" })).toHaveAttribute(
    "aria-checked",
    "true",
  );
  await family.getByRole("radio", { name: "keys" }).click();
  await page.getByRole("radio", { name: "rhodes" }).click();
  await page.getByRole("button", { name: /Save 3 corrections/ }).click();
  await expect
    .poll(() => latestReview(request, item.id))
    .toMatchObject({
      action: "edit",
      field_diff: {
        family: { from: "drums", to: "keys" },
        leaf: { from: "kick", to: "rhodes" },
        bpm: { from: 100, to: 50 },
      },
    });
  const saved = await (await request.get(`/v1/items/${item.id}`)).json();
  expect(saved.status).toBe("corrected");
});

test("invalid label values block saving", async ({ page, request }) => {
  const item = await labelItem(request);
  await page.goto(`/items/${item.id}`);
  await page.getByRole("textbox", { name: "bpm" }).fill("999");
  await expect(
    page.getByRole("alert").filter({ hasText: "≤ 240" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: /Save 1 correction/ }),
  ).toBeDisabled();
});

test("path assets stream from the asset root or explain why not", async ({
  page,
  request,
}) => {
  await page.goto("/items/seed-paths");
  const audio = page.locator("article audio").first();
  await expect(audio).toBeVisible();
  const src = await audio.getAttribute("src");
  expect(src).toBe("/v1/items/seed-paths/assets/0");
  const partial = await request.get(src!, { headers: { Range: "bytes=0-3" } });
  expect(partial.status()).toBe(206);
  await expect(page.getByText("File not found on this machine")).toBeVisible();
});

test("mark unsure while correcting labels", async ({ page, request }) => {
  const item = await seed(request, {
    project: "labels-e2e",
    output: {
      family: "drums",
      leaf: "kick",
      type: "loop",
      bpm: 100,
      key: null,
    },
    fields: LABEL_FIELDS,
    options: ["correct", "unsure"],
  });
  await page.goto(`/items/${item.id}`);
  // flags replace the separate choice buttons
  await expect(page.getByRole("button", { name: /^Choose / })).toHaveCount(0);
  await page
    .getByRole("radiogroup", { name: "type" })
    .getByRole("radio", { name: "one_shot" })
    .click();
  await page.locator("h1").first().click();
  await page.keyboard.press("?");
  const unsure = page
    .getByRole("group", { name: "Mark result as" })
    .getByRole("button", { name: /unsure/ });
  await expect(unsure).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.getByRole("button", { name: /Save 1 correction · unsure/ }),
  ).toBeVisible();
  await page.keyboard.press("Enter");
  await expect
    .poll(() => latestReview(request, item.id))
    .toMatchObject({
      action: "edit",
      choice: "unsure",
      field_diff: { type: { from: "loop", to: "one_shot" } },
    });
});

test("flag without changes confirms the labels", async ({ page, request }) => {
  const item = await seed(request, {
    project: "labels-e2e",
    output: {
      family: "drums",
      leaf: "kick",
      type: "loop",
      bpm: 100,
      key: null,
    },
    fields: LABEL_FIELDS,
    options: ["unsure"],
  });
  await page.goto(`/items/${item.id}`);
  await page
    .getByRole("group", { name: "Mark result as" })
    .getByRole("button", { name: /unsure/ })
    .click();
  await page.getByRole("button", { name: /Confirm all · unsure/ }).click();
  await expect
    .poll(() => latestReview(request, item.id))
    .toMatchObject({
      action: "approve",
      choice: "unsure",
      field_diff: {},
    });
});
