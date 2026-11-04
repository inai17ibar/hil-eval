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
