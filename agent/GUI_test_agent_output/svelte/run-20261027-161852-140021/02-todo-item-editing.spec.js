import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  page.on('pageerror', (err) => console.error(`APP_PAGE_ERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error(`APP_CONSOLE_ERROR: ${msg.text()}`);
  });
  await page.goto(process.env.BASE_URL || 'http://localhost:3000');
});

test('should allow editing an existing todo', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Verify that double-clicking a todo enters edit mode and saves changes on Enter.' },
    { type: 'expected', description: 'Label text updates after editing and blurring/pressing Enter.' }
  );

  const newTodo = page.getByPlaceholder('What needs to be done?');
  await newTodo.fill('Initial Task');
  await newTodo.press('Enter');

  const todoLabel = page.locator('.todo-list li label');
  await todoLabel.dblclick();

  const editInput = page.getByLabel('Edit todo');
  await expect(editInput).toBeFocused();
  await editInput.fill('Updated Task');
  await editInput.press('Enter');

  await expect(todoLabel).toHaveText('Updated Task');
  await expect(editInput).not.toBeVisible();
});

test('should remove the todo if the edited text is empty', async ({ page }) => {
  test.info().annotations.push(
    { type: 'purpose', description: 'Verify that clearing the text during an edit removes the item.' },
    { type: 'expected', description: 'The item is spliced from the items list when blurred with empty value.' }
  );

  await page.getByPlaceholder('What needs to be done?').fill('Deletable');
  await page.getByPlaceholder('What needs to be done?').press('Enter');

  await page.locator('.todo-list li label').dblclick();
  const editInput = page.getByLabel('Edit todo');
  await editInput.fill('');
  await editInput.blur();

  await expect(page.locator('.todo-list li')).toHaveCount(0);
});
