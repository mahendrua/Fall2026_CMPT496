import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should filter items and update count', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Tests currentFilter router effect and derived filtered/numActive states' },
    { type: 'expected', description: 'Active and Completed filters correctly show/hide items and update counter' }
  );

  const mainInput = page.getByPlaceholder('What needs to be done?');
  await mainInput.fill('Active Todo');
  await mainInput.press('Enter');
  await mainInput.fill('Completed Todo');
  await mainInput.press('Enter');

  const todos = page.locator('.todo-list li');
  await todos.filter({ hasText: 'Completed Todo' }).getByRole('checkbox').check();

  await expect(page.locator('.todo-count')).toHaveText('1 item left');

  // Active Filter
  await page.getByRole('link', { name: 'Active' }).click();
  await expect(todos).toHaveCount(1);
  await expect(todos).toHaveText('Active Todo');

  // Completed Filter
  await page.getByRole('link', { name: 'Completed' }).click();
  await expect(todos).toHaveCount(1);
  await expect(todos).toHaveText('Completed Todo');

  // All Filter
  await page.getByRole('link', { name: 'All' }).click();
  await expect(todos).toHaveCount(2);
});
