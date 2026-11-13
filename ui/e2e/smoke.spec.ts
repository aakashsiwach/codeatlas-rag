import { expect, test } from "@playwright/test";

const shots = process.env.SCREENSHOTS_DIR; // set to save screenshots, e.g. SCREENSHOTS_DIR=screenshots

async function snap(page: import("@playwright/test").Page, name: string) {
  if (shots) await page.screenshot({ path: `${shots}/${name}.png` });
}

test("streams a cited answer, opens the cited source, and shows a don't-know answer", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("open-settings")).toContainText("fast · vector · k=5");
  await expect(page.getByTestId("setup-fast")).toHaveAttribute("aria-checked", "true");
  await snap(page, "1-empty");

  await page.getByTestId("question").fill("Where is the payment retry logic?");
  await page.getByTestId("send").click();
  const answer = page.getByTestId("answer").last();
  await expect(answer).toContainText("Offline demo answer");
  await expect(answer.getByTestId("cite-1")).toBeVisible();
  await expect(answer.getByTestId("answer-meta")).toContainText(/fast · vector · retrieval \d+ ms · total \d+ ms/);

  await answer.getByTestId("cite-1").click();
  const viewer = page.getByTestId("source-viewer");
  await expect(viewer).toBeVisible();
  await expect(viewer.getByTestId("highlighted-line").first()).toBeVisible();
  await snap(page, "2-answer-and-source");

  await page.getByTestId("question").fill("How are Kafka consumer offsets committed?");
  await page.getByTestId("send").click();
  await expect(page.getByTestId("dont-know")).toContainText("I don't know based on the indexed code.");
  await expect(page.getByTestId("dont-know")).toContainText("Closest matches");
  await snap(page, "3-dont-know");

  await page.getByTestId("setup-quality").click();
  await page.getByTestId("question").fill("Where is the payment retry logic?");
  await page.getByTestId("send").click();
  await expect(page.getByTestId("turn").last().getByTestId("answer-meta")).toContainText(/quality · hybrid\+graph · retrieval \d+ ms/);
  await page.getByTestId("setup-fast").click();

  await page.getByTestId("open-settings").click();
  const settings = page.getByTestId("settings");
  await expect(settings).toContainText("hashing-512");
  await expect(settings).toContainText("fake:offline");
  await snap(page, "4-settings");
  await page.getByTestId("mode-select").selectOption("hybrid+rerank");
  await page.keyboard.press("Escape");
  await page.mouse.click(10, 400);
  await expect(page.getByTestId("open-settings")).toContainText("custom · hybrid+rerank");
  await expect(page.getByTestId("setup-fast")).toHaveAttribute("aria-checked", "false");
});
