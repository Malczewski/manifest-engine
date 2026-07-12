package com.manifestengine.viz.ui

import androidx.compose.runtime.Composable
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument

object Routes {
    const val LIBRARY = "library"
    const val SERVERS = "servers"
    const val READER = "reader/{bookId}"
    fun reader(bookId: String) = "reader/$bookId"
}

@Composable
fun AppNav() {
    val nav = rememberNavController()
    NavHost(navController = nav, startDestination = Routes.LIBRARY) {
        composable(Routes.LIBRARY) {
            LibraryScreen(
                onOpenServers = { nav.navigate(Routes.SERVERS) },
                onOpenBook = { bookId -> nav.navigate(Routes.reader(bookId)) },
            )
        }
        composable(Routes.SERVERS) {
            ServersScreen(onBack = { nav.popBackStack() })
        }
        composable(
            Routes.READER,
            arguments = listOf(navArgument("bookId") { type = NavType.StringType }),
        ) { entry ->
            ReaderScreen(
                bookId = entry.arguments?.getString("bookId").orEmpty(),
                onBack = { nav.popBackStack() },
            )
        }
    }
}
