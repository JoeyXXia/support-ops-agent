import { chromium, expect } from "@playwright/test";
import { mkdir, mkdtemp } from "node:fs/promises";
import { resolve } from "node:path";
import { setTimeout } from "node:timers/promises";

const root = resolve("output/day28-demo");
await mkdir(root, { recursive: true });
const dir = await mkdtemp(resolve(root, "capture-"));
const base = new URL(process.env.SUPPORTOPS_E2E_URL || "http://127.0.0.1:5173");
if (base.protocol !== "http:" || base.hostname !== "127.0.0.1") {
  throw new Error("Recording requires the local Compose demo on 127.0.0.1");
}
const browser = await chromium.launch();
const size = { width: 1200, height: 800 };
const context = await browser.newContext({
  viewport: size,
  recordVideo: { dir, size },
});
const page = await context.newPage();
page.setDefaultTimeout(10000);
const video = page.video();
const start = performance.now();
const marks = {};

async function hold(name, locator, until) {
  await expect(locator).toBeVisible();
  await locator.scrollIntoViewIfNeeded();
  marks[name] = Math.round((performance.now() - start) / 100) / 10;
  await setTimeout(Math.max(0, until * 1000 - (performance.now() - start)));
}
async function submit(question) {
  await page.getByLabel("你的问题").fill(question);
  await page.getByRole("button", { name: "开始排查", exact: true }).click();
  await expect(page).toHaveURL(/#run\/[0-9a-f-]+$/);
}
async function savedRun() {
  const id = new URL(page.url()).hash.split("/")[1];
  const response = await page.request.get(
    new URL(`/api/runs/${id}`, base).href,
  );
  expect(response.ok()).toBeTruthy();
  return response.json();
}
try {
  await page.goto(base.href);
  await page.getByLabel("口令").fill("local-alpha-demo");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await hold("mock_entry", page.getByText("本地 mock", { exact: true }), 12);
  await page.getByLabel("使用文档引用演示").check();
  await page.getByRole("combobox", { name: "产品版本" }).selectOption("2.0");
  await submit("starter配额是多少？");
  await expect(page.getByRole("heading", { name: "运行：完成" })).toBeVisible();
  const [citation] = (await savedRun()).citations;
  expect(citation.product_version).toBe("2.0");
  await page
    .getByRole("link", { name: /^查看引用：/ })
    .first()
    .click();
  const source = page.getByRole("region", { name: "引用原文" });
  await expect(source.locator(".citation-body")).toHaveText(citation.body);
  await hold("citation", source, 38);
  await page.goto(base.href);
  await page.getByLabel(/生成待审核工单草稿/).check();
  await submit("ws-alpha 429，转人工排查");
  await expect(
    page.getByRole("heading", { name: "运行：等待审批" }),
  ).toBeVisible();
  expect((await savedRun()).ticket).toBeFalsy();
  const approve = page.getByRole("button", { name: "批准并创建工单" });
  await hold("pending_approval", approve, 56);
  await approve.click();
  await expect(page.getByRole("heading", { name: "运行：完成" })).toBeVisible();
  const approved = await savedRun();
  expect(approved.reason).toBe("ticket_created");
  await page.getByRole("link", { name: "查看工单当前详情" }).click();
  await hold(
    "ticket_created",
    page.getByText(approved.ticket.ticket_id, { exact: true }),
    79,
  );
  await page.goto(base.href);
  await page.getByLabel(/生成待审核工单草稿/).check();
  await submit("ws-alpha 429，准备拒绝此草稿");
  const reject = page.getByRole("button", { name: "拒绝草稿" });
  await hold("pending_rejection", reject, 95);
  await reject.click();
  await expect(page.getByRole("heading", { name: "运行：完成" })).toBeVisible();
  await page.reload();
  await expect(page.getByText("已拒绝", { exact: true })).toBeVisible();
  expect(await savedRun()).toMatchObject({
    reason: "approval_rejected",
    ticket: null,
  });
  await hold(
    "approval_rejected",
    page.getByText("已拒绝", { exact: true }),
    119,
  );
} finally {
  await context.close();
  await browser.close();
}
console.log(JSON.stringify({ video: await video.path(), marks }, null, 2));
