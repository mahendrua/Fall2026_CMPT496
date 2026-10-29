const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should toggle all items state at once', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Click the toggle-all checkbox to complete or reactivate all items.' },
    { type: 'expected', description: 'All list items receive the completed class. Checking toggle-all when all items are finished should uncheck them all.' }
  );

  const input = page.getByPlaceholder('What needs to be done?');
  await input.fill('Item 1');
  await input.press('Enter');
  await input.fill('Item 2');
  await input.press('Enter');

  const toggleAll = page.getByLabel('Mark all as complete');
  
  // Toggle All On
  await toggleAll.click();
  await expect(page.locator('.todo-list li.completed')).toHaveCount(2);
  await expect(page.locator('.todo-count')).toHaveText('0 items left');

  // Toggle All Off
  await toggleAll.click();
  await expect(page.locator('.todo-list li.completed')).toHaveCount(0);
  await expect(page.locator('.todo-count')).toHaveText('2 items left');
});
