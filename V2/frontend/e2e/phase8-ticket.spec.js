import fs from "node:fs";
import { expect, test } from "@playwright/test";

const tokens = JSON.parse(fs.readFileSync(new URL("../../.local/test_tokens.json", import.meta.url), "utf8"));

test("assigned engineer rechecks an incomplete ticket without closing it", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Merchant access token").fill(tokens["admin-a"]);
  await page.getByRole("button", { name: "Start conversation" }).click();
  await expect(page.getByText("Current Agent: CUSTOMER")).toBeVisible();

  await page.getByRole("button", { name: "Request engineer" }).click();
  await expect(page.getByRole("heading", { name: "Engineer Ticket" })).toBeVisible();

  await page.getByLabel("Engineer access token").fill(tokens["engineer-a"]);
  await page.getByRole("button", { name: "Load assigned tickets" }).click();
  await page.locator("button.ticket").first().click();
  await expect(page.getByRole("heading", { name: "Assigned Ticket" })).toBeVisible();
  await page.getByRole("button", { name: "Recheck business result" }).click();
  await expect(page.getByText("Ticket status: open")).toBeVisible();
  await expect(page.getByText("NEEDS_INFO", { exact: false }).last()).toBeVisible();
});
