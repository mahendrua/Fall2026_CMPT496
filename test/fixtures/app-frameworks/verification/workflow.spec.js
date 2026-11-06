const { test, expect } = require('@playwright/test');
test.beforeEach(async ({ page }) => {
    page.on('console', message => { if (message.type() === 'error') console.error('APP_CONSOLE_ERROR:' + message.text()); });
    page.on('pageerror', error => console.error('APP_PAGE_ERROR:' + error.message));
});
test('application loads and browser interactions are visible', async ({ page }) => {
    await page.goto(process.env.BASE_URL);
    await expect(page.getByRole('heading')).toHaveText(/^Checkpoint (Vite|Next|Express|Flask|Static) Fixture$/);
    for (let i = 1; i <= 16; i++) {
        await page.getByRole('button', { name: 'Increase', exact: true }).click();
        await expect(page.locator('output')).toHaveText(String(i));
    }
});
test('controlled failure still produces screenshot evidence', async ({ page }) => {
    await page.goto(process.env.BASE_URL);
    await expect(page.locator('output')).toHaveText('99');
});
