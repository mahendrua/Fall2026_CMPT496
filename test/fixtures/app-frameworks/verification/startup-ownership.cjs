const fs = require('node:fs');
const path = require('node:path');
const { LocalGuiApp } = require('../../../../guiLocalApp');
(async () => {
    const results=[];
    for(const name of ['vite','next','express','python','static']) {
        const app=new LocalGuiApp();
        try { const startup=await app.start(path.resolve(__dirname,'..',name));results.push({name,framework:startup.framework,port:startup.port,ownedProcessVerified:!!app.active}); }
        finally { await app.stop(); }
    }
    fs.writeFileSync(path.join(__dirname,'evidence/startup-ownership.json'),JSON.stringify(results,null,2));
    console.log(JSON.stringify(results));
})().catch(error=>{console.error(error);process.exitCode=1;});
