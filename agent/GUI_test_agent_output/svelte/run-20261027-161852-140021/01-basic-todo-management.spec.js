import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should add multiple items and toggle their completion', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Validate that the user can add new items and mark them as complete.' },
    { type: 'expected', description: 'Items are added to the list and completed items gain the completed CSS class.' }
  );

  const newTodo = page.getByPlaceholder('What needs to be done?');
  await newTodo.fill('Buy milk');
  await newTodo.press('Enter');
  await newTodo.fill('Walk the dog');
  await newTodo.press('Enter');

  const todoItems = page.locator('.todo-list li');
  await expect(todoItems).toHaveCount(2);
  await expect(todoItems.nth(0)).toHaveText('Buy milk');

  const firstCheckbox = todoItems.nth(0).locator('.toggle');
  await firstCheckbox.check();
  await expect(todoItems.nth(0)).toHaveClass(/completed/);
  await expect(todoItems.nth(1)).not.toHaveClass(/completed/);

  await expect(page.locator('.todo-count')).toContainText('1 item left');
});

test('should remove a todo item when clicking destroy', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Validate item removal via the destroy button.' },
    { type: 'expected', description: 'The item is removed from the list and the UI updates accordingly.' }
  );

  await page.getByPlaceholder('What needs to be done?').fill('Task to remove');
  await page.getByPlaceholder('What needs to be done?').press('Enter');

  const todoItem = page.locator('.todo-list li');
  await todoItem.hover();
  await todoItem.getByLabel('Delete').click();

  await expect(todoItem).toHaveCount(0);
  await expect(page.locator('.main')).toBeHidden();
});
