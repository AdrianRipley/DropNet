// SPDX-License-Identifier: GPL-3.0-or-later
plugins { id("com.android.application") }

android {
    namespace = "org.dropnet.demo"
    compileSdk { version = release(36) { minorApiLevel = 1 } }
    buildToolsVersion = "36.1.0"
    defaultConfig {
        applicationId = "org.dropnet.demo"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "0.4.0-demo"
        testInstrumentationRunner = "org.dropnet.demo.SelfTestInstrumentation"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildTypes {
        release { isMinifyEnabled = false }
    }
}
dependencies { implementation(project(":core")) }
