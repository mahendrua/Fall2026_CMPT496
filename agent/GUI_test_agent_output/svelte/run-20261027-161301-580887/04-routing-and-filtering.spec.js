const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should filter todos by status', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Use Active and Completed filters to view specific subsets of tasks.' },
    { type: 'expected', description: 'The URL hash changes, the selected class moves between links, and only relevant items are visible in the list.' }
  );

  const input = page.getByPlaceholder('What needs to be done?');
  await input.fill('Active Task');
  await input.press('Enter');
  await input.fill('Completed Task');
  await input.press('Enter');

  await page.locator('.todo-list li').filter({ hasText: 'Completed Task' }).locator('.toggle').check();

  // Filter Active
  await page.getByRole('link', { name: 'Active' }).click();
  await expect(page).toHaveURL(/#\/active/);
  await expect(page.locator('.todo-list li')).toHaveCount(1);
  await expect(page.locator('.todo-list li')).toHaveText('Active Task');

  // Filter Completed
  await page.getByRole('link', { name: 'Completed' }).click();
  await expect(page).toHaveURL(/#\/completed/);
  await expect(page.locator('.todo-list li')).toHaveCount(1);
  await expect(page.locator('.todo-list li')).toHaveText('Completed Task');

  // Filter All
  await page.getByRole('link', { name: 'All' }).click();
  await expect(page.locator('.todo-list li')).toHaveCount(2);
});
