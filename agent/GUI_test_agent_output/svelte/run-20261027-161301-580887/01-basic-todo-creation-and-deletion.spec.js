const { test, expect } = require('@playwright/test');

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should allow adding and deleting todo items', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Add items and verify they appear in the list; delete an item and verify it is removed.' },
    { type: 'expected', description: 'Items are added to the DOM list and the counter in the footer updates correctly. Removing an item removes its li element.' }
  );

  const input = page.getByPlaceholder('What needs to be done?');
  
  // Add items
  await input.fill('Buy milk');
  await input.press('Enter');
  await input.fill('Feed the cat');
  await input.press('Enter');

  const todoItems = page.locator('.todo-list li');
  await expect(todoItems).toHaveCount(2);
  await expect(todoItems.nth(0)).toHaveText('Buy milk');
  await expect(todoItems.nth(1)).toHaveText('Feed the cat');
  await expect(page.locator('.todo-count')).toHaveText('2 items left');

  // Delete an item
  await todoItems.nth(0).hover();
  await todoItems.nth(0).getByRole('button', { name: 'Delete' }).click();

  await expect(todoItems).toHaveCount(1);
  await expect(todoItems.nth(0)).toHaveText('Feed the cat');
  await expect(page.locator('.todo-count')).toHaveText('1 item left');
});
