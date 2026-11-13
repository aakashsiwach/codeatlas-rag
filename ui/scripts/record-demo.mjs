// Records the README demo against a running CodeAtlas (e.g. `codeatlas demo --port 8010`, offline fake LLM):
// fast answer, impact question in fast then quality, a citation opening the source viewer, and a refusal.
// Writes a .webm video, timing marks (marks.json) and screenshots. scripts/make-demo-gif.sh turns them into
// docs/demo.gif.
//
//   BASE_URL=http://127.0.0.1:8010 OUT_DIR=demo-recording node scripts/record-demo.mjs
import { mkdirSync, renameSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "@playwright/test";

const base = process.env.BASE_URL ?? "http://127.0.0.1:8010";
const out = process.env.OUT_DIR ?? "demo-recording";
const shots = join(out, "screenshots");
mkdirSync(shots, { recursive: true });

const viewport = { width: 1280, height: 760 };
const browser = await chromium.launch();
const context = await browser.newContext({ viewport, recordVideo: { dir: out, size: viewport } });
const page = await context.newPage();
const t0 = Date.now();
const marks = {};
const mark = (name) => (marks[name] = (Date.now() - t0) / 1000);
const pause = (ms) => page.waitForTimeout(ms);
const shot = (name) => page.screenshot({ path: join(shots, `${name}.png`) });

let asked = 0;
async function ask(question, setup) {
  if (setup) await page.getByTestId(`setup-${setup}`).click();
  await page.getByTestId("question").pressSequentially(question, { delay: 28 });
  await pause(300);
  await page.getByTestId("send").click();
  asked += 1;
}
const lastTurn = () => page.getByTestId("turn").nth(asked - 1);
const finished = (timeout = 30_000) => lastTurn().getByTestId("answer-meta").waitFor({ timeout });

await page.goto(base);
await page.getByTestId("setup-fast").waitFor();
await pause(1200);
mark("start");

await ask("Who calls Owner.addPet?", "fast");
await finished();
await pause(1800);
await shot("1-fast-answer");

await ask("What breaks if I change the Owner entity?", "fast");
await finished();
await pause(1500);

await ask("What breaks if I change the Owner entity?", "quality");
await lastTurn().getByTestId("progress").waitFor();
mark("quality_wait_start");
await pause(3500);
await shot("2-quality-progress");
await finished(180_000);
mark("quality_wait_end");
await pause(1800);
await shot("3-quality-answer");

await lastTurn().getByTestId("cite-2").first().click(); // a code block (block 1 is the graph report)
await page.getByTestId("source-viewer").waitFor();
await pause(2800);
await shot("4-source-viewer");
await page.getByRole("button", { name: "Close source viewer" }).click().catch(() => {});
await pause(600);

await ask("How are Kafka consumer offsets committed?", "fast");
await lastTurn().getByTestId("dont-know").waitFor({ timeout: 30_000 });
await pause(2600);
await shot("5-dont-know");

await page.getByTestId("open-settings").click();
await page.getByTestId("settings").waitFor();
await pause(800);
await shot("6-settings");
mark("end");

const video = page.video();
await context.close();
await browser.close();
renameSync(await video.path(), join(out, "demo.webm"));
writeFileSync(join(out, "marks.json"), JSON.stringify(marks, null, 2));
console.log("recorded", join(out, "demo.webm"), marks);
