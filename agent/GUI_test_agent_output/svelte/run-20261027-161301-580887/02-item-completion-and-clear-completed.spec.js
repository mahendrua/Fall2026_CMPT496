const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should toggle items and clear completed', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Mark items as completed and use the "Clear completed" button to remove them.' },
    { type: 'expected', description: 'Toggled items get the "completed" class. Clear completed button is hidden until an item is finished and removes finished items upon click.' }
  );

  const input = page.getByPlaceholder('What needs to be done?');
  await input.fill('Task A');
  await input.press('Enter');
  await input.fill('Task B');
  await input.press('Enter');

  const clearButton = page.getByRole('button', { name: 'Clear completed' });
  await expect(clearButton).toBeHidden();

  // Complete Task A
  const taskA = page.locator('.todo-list li').filter({ hasText: 'Task A' });
  await taskA.locator('.toggle').check();

  await expect(taskA).toHaveClass(/completed/);
  await expect(page.locator('.todo-count')).toHaveText('1 item left');
  await expect(clearButton).toBeVisible();

  // Clear completed
  await clearButton.click();
  await expect(page.locator('.todo-list li')).toHaveCount(1);
  await expect(page.locator('.todo-list li')).toHaveText('Task B');
  await expect(clearButton).toBeHidden();
});
