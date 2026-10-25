import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Release signing. Create android/keystore.properties (it is git-ignored, never commit it):
//   storeFile=/absolute/path/to/eternal-timer.jks
//   storePassword=...
//   keyAlias=...
//   keyPassword=...
// Without that file the release build is simply unsigned (which is what F-Droid wants).
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "com.eternaltimer.app"
    compileSdk = 34

    defaultConfig {
        // Decide this BEFORE the first public release: changing it later means existing installs can't update.
        // (Only this line needs to change; the code's package name can stay as it is.)
        applicationId = "com.eternaltimer.app"
        minSdk = 26
        targetSdk = 34
        versionCode = 300          // must increase with every release
        versionName = "3.0.0"
    }

    signingConfigs {
        create("release") {
            if (keystoreProps.isNotEmpty()) {
                storeFile = file(keystoreProps.getProperty("storeFile"))
                storePassword = keystoreProps.getProperty("storePassword")
                keyAlias = keystoreProps.getProperty("keyAlias")
                keyPassword = keystoreProps.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = if (keystoreProps.isNotEmpty()) signingConfigs.getByName("release") else null
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    // The sync engine is pure Kotlin (no Android imports) so it can be unit-tested on a plain JVM.
    sourceSets {
        getByName("main") {
            java.srcDir("../engine")
        }
    }
}

// No third-party dependencies on purpose: HttpURLConnection, javax.crypto and the platform
// widgets are all that is needed.
dependencies {
}
