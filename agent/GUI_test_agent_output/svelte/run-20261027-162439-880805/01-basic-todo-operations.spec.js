import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should allow adding and deleting todos', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Tests the addItem and removeItem handlers' },
    { type: 'expected', description: 'Todos are added to the list and removed when the destroy button is clicked' }
  );

  const input = page.getByPlaceholder('What needs to be done?');
  await input.fill('Buy milk');
  await input.press('Enter');
  await input.fill('Clean room');
  await input.press('Enter');

  const todoItems = page.locator('.todo-list li');
  await expect(todoItems).toHaveCount(2);
  await expect(todoItems.first()).toHaveText('Buy milk');

  await todoItems.filter({ hasText: 'Buy milk' }).getByLabel('Delete').click();
  await expect(todoItems).toHaveCount(1);
  await expect(todoItems).toHaveText('Clean room');
});

test('should allow toggling completion status', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Tests the checkbox onchange handler in Item.svelte' },
    { type: 'expected', description: 'The completed class is applied to the list item' }
  );

  const input = page.getByPlaceholder('What needs to be done?');
  await input.fill('Buy milk');
  await input.press('Enter');

  const todoItem = page.locator('.todo-list li');
  const checkbox = todoItem.getByRole('checkbox');
  
  await checkbox.check();
  await expect(todoItem).toHaveClass(/completed/);
  
  await checkbox.uncheck();
  await expect(todoItem).not.toHaveClass(/completed/);
});
