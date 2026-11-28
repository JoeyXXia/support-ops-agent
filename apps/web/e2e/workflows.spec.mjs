import { expect, test } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page
    .getByRole("combobox", { name: "账号", exact: true })
    .selectOption("user-alpha");
  await page.getByLabel("口令").fill("local-alpha-demo");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByText("本地 mock", { exact: true })).toBeVisible();
});

async function submit(page, question) {
  await page.getByLabel("你的问题").fill(question);
  await page.getByRole("button", { name: "开始排查" }).click();
  await expect(page).toHaveURL(/#run\/[0-9a-f-]+$/);
}

async function savedRun(page) {
  const runId = new URL(page.url()).hash.split("/")[1];
  const response = await page.request.get(`/api/runs/${runId}`);
  expect(response.status()).toBe(200);
  return response.json();
}

test("grounded answer opens the saved citation after refresh", async ({
  page,
}) => {
  await page.getByLabel("使用文档引用演示").check();
  await page.getByRole("combobox", { name: "产品版本" }).selectOption("2.0");
  await submit(page, "starter配额是多少？");
  await expect(page.getByRole("heading", { name: "运行：完成" })).toBeVisible();
  const run = await savedRun(page);
  expect(run.mode).toBe("mock");
  expect(run.answer).toMatch(/starter/i);
  const [citation] = run.citations;
  expect(citation.product_version).toBe("2.0");
  await page
    .getByRole("link", { name: /^查看引用：/ })
    .first()
    .click();
  await expect(page).toHaveURL(new RegExp(`/citation/${citation.chunk_id}$`));
  await page.reload();
  const source = page.getByRole("region", { name: "引用原文" });
  await expect(source).toBeVisible();
  await expect(source).toContainText("产品 2.0");
  await expect(source.locator(".citation-body")).toHaveText(citation.body);
});

test("approval creates a ticket that can be opened", async ({ page }) => {
  const question = "ws-alpha 429，E2E 批准样例";
  await page.getByLabel(/生成待审核工单草稿/).check();
  await submit(page, question);
  await expect(
    page.getByRole("heading", { name: "运行：等待审批" }),
  ).toBeVisible();
  expect((await savedRun(page)).ticket).toBeFalsy();
  await page.getByRole("button", { name: "批准并创建工单" }).click();
  await expect(page.getByRole("heading", { name: "运行：完成" })).toBeVisible();
  const run = await savedRun(page);
  expect(run.reason).toBe("ticket_created");
  await page.getByRole("link", { name: "查看工单当前详情" }).click();
  await expect(page.getByRole("heading", { name: "工单详情" })).toBeVisible();
  await expect(
    page.getByText(run.ticket.ticket_id, { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(question, { exact: true })).toBeVisible();
});

test("rejection is stored and produces no ticket", async ({ page }) => {
  await page.getByLabel(/生成待审核工单草稿/).check();
  await submit(page, "ws-alpha 429，E2E 拒绝样例");
  await expect(
    page.getByRole("heading", { name: "运行：等待审批" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "拒绝草稿" }).click();
  await expect(page.getByText("已拒绝", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "运行：完成" })).toBeVisible();
  await page.reload();
  await expect(page.getByText("已拒绝", { exact: true })).toBeVisible();
  expect(await savedRun(page)).toMatchObject({
    reason: "approval_rejected",
    ticket: null,
  });
  await expect(
    page.getByRole("link", { name: "查看工单当前详情" }),
  ).toHaveCount(0);
});

test("browser reconnects with the last durable event and refreshes the same ticket", async ({
  page,
}) => {
  let cursor;
  const resumed = page.waitForRequest(
    async (request) =>
      request.url().includes("/events") &&
      cursor !== undefined &&
      (await request.allHeaders())["last-event-id"] === cursor,
  );
  let submissions = 0;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/runs"
    )
      submissions++;
  });
  await page.route(
    "**/api/runs/*/events",
    async (route) => {
      const response = await route.fetch();
      const body = await response.text();
      const frame = body
        .split("\n\n")
        .find((entry) => entry.includes("event: run.snapshot"));
      // A very fast worker may have finished drafting before the browser subscribed.
      // In that case the next request will replay the saved approval with the same ID.
      const partial =
        frame ||
        body
          .split("\n\n")[0]
          .replace("event: approval.required", "event: run.snapshot")
          .replace('"type": "approval.required"', '"type": "run.snapshot"');
      cursor = /^id: (\d+)/m.exec(partial)?.[1];
      expect(cursor).toBeDefined();
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: partial + "\n\n",
      });
    },
    { times: 1 },
  );
  await page.getByLabel(/生成待审核工单草稿/).check();
  await submit(page, "ws-alpha 429，SSE 重连验证");
  await resumed;
  await expect(
    page.getByRole("heading", { name: "运行：等待审批" }),
  ).toBeVisible();
  const url = page.url();
  await page.getByRole("button", { name: "批准并创建工单" }).click();
  await expect(page.getByRole("heading", { name: "运行：完成" })).toBeVisible();
  const ticket = (await savedRun(page)).ticket.ticket_id;
  await page.reload();
  await expect(page).toHaveURL(url);
  await expect(
    page.getByRole("link", { name: "查看工单当前详情" }),
  ).toHaveCount(1);
  expect((await savedRun(page)).ticket.ticket_id).toBe(ticket);
  expect(submissions).toBe(1);
});
