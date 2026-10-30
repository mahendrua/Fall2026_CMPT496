const { test, expect } = require('@playwright/test');
test('visible browser interactions and navigation', async ({ page }) => {
    await page.goto(process.env.BASE_URL);
    await expect(page.getByRole('heading')).toHaveText('Live Browser Evidence');
    await page.getByLabel('Message').fill('Visible input from Playwright');
    for (let i = 1; i <= 16; i++) {
        await page.getByRole('button', { name: 'Change color' }).click();
        await expect(page.locator('output')).toHaveText(String(i));
    }
    await page.goto(new URL('/second', process.env.BASE_URL).toString());
    await expect(page.getByRole('heading')).toHaveText('Navigation Evidence');
});
test('failure screenshots remain available', async ({ page }) => {
    await page.goto(process.env.BASE_URL);
    await expect(page.locator('output')).toHaveText('99');
});
