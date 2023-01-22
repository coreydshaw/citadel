# -*- coding: utf-8 -*-
"""
** Populate blaseball SQL server, as of Delta Eon **

Created:        2023-01-15
Last Update:    2023-01-21 

@author: ifhbiff
"""
import numpy as np
import pandas as pd
import sqlalchemy
from datetime import datetime
from sqlalchemy.orm import sessionmaker
import configparser

def read_config():
    config = configparser.ConfigParser()
    config.read('datablase_config.ini')
    return config


def pick_rating(categoryRatings, category):
    for item in categoryRatings:
        if item["name"] == category:
            return item["stars"]


def pick_attribute(attributes, name):
    for item in attributes:
        if item["name"] == name:
            return item["value"]
   

def raw_rating(attributes, category):
    if category == "batting":
        attr1 = pick_attribute(attributes, "Sight")
        attr2 = pick_attribute(attributes, "Thwack")
        attr3 = pick_attribute(attributes, "Ferocity")
    elif category == "pitching":
        attr1 = pick_attribute(attributes, "Control")
        attr2 = pick_attribute(attributes, "Stuff")
        attr3 = pick_attribute(attributes, "Guile")
    elif category == "defense":
        attr1 = pick_attribute(attributes, "Reach")
        attr2 = pick_attribute(attributes, "Magnet")
        attr3 = pick_attribute(attributes, "Reflex")
    elif category == "running":
        attr1 = pick_attribute(attributes, "Hustle")
        attr2 = pick_attribute(attributes, "Stealth")
        attr3 = pick_attribute(attributes, "Dodge")
    elif category == "vibes":
        attr1 = pick_attribute(attributes, "Thrive")
        attr2 = pick_attribute(attributes, "Survive")
        attr3 = pick_attribute(attributes, "Drama")
    return 5 * (attr1 + attr2 + attr3) / 3


def get_object_timestamp(object_type):
    result = conn.execute("SELECT {0}_timestamp::character varying from delta_data.api_loads_meta".format(object_type))
    values = result.fetchall()
    #ugly way to pull first field, first value from the list
    next_timestamp = values[0][0] + 'Z'
    return next_timestamp.replace(' ','T') 


attribute_list = [
    "Sight",
    "Thwack",
    "Ferocity",
    "Control",
    "Stuff",
    "Guile",
    "Reach",
    "Magnet",
    "Reflex",
    "Hustle",
    "Stealth",
    "Dodge",
    "Thrive",
    "Survive",
    "Drama"
]

append_game_states = [
    "awayScore",
    "homeScore",
    "inning",
    "ballsNeeded",
    "strikesNeeded",
    "outsNeeded",
    "totalBases",
    "shame"
]

append_events = [
    "data.changedState.balls",
    "data.changedState.strikes",
    "data.changedState.outs",
    "data.changedState.awayScore",
    "data.changedState.homeScore",
    "data.changedState.inning",
    "data.changedState.batter.id",
    "data.changedState.pitcher.id"
]

read_config()
print('Connecting to Postgres')
engine = sqlalchemy.create_engine('postgresql+psycopg2://{0}:{1}@{2}:{3}/{4}'.format
                                  (config['DATABLASE']['user'],config['DATABLASE']['password'],config['DATABLASE']['host'],
                                   config['DATABLASE']['port'],config['DATABLASE']['db']))
Session = sessionmaker(bind=engine)
session = Session()
conn = engine.connect()


def playersMain():
    print('Starting playersMain()')
    pages = 1
    
    next_timestamp = get_object_timestamp('player')
    
    players_processed = pd.read_sql_query('SELECT player_id, valid_from::character varying FROM delta_data.players',conn)
    players_processed["valid_from"] = players_processed["valid_from"].str.replace(' ','T') + 'Z'
    
    #loop through results from API, starting at next_timestamp each time, until no more records 
    while True:
    
        raw_players = pd.read_json("https://api2.sibr.dev/chronicler/v0/versions?kind=player&order=asc&after={0}".format(next_timestamp))  
  
        
        if raw_players.shape[0] == 1:
            print('No additional records found beyond this timestamp.')
            break
        
        
        normPlayer = pd.json_normalize(raw_players["items"])
        player = normPlayer[["entity_id", "data.name","valid_from","valid_to"]].copy()
        player.rename(columns={"entity_id":"player_id", "data.name":"player_name","valid_to":"valid_until"}, inplace=True) 
        player["temprosterlocation"] = normPlayer["data.rosterSlots"].apply(lambda x: ";".join([y['location'] for y in x]))
        player["temprosterindex"] = normPlayer["data.rosterSlots"].apply(lambda x: [y['orderIndex'] for y in x])
        player["temprosterindex"] = player["temprosterindex"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
        player["tempteamid"] = normPlayer["data.team.id"].apply(lambda x: x["id"] if type(x) == dict else x)
        player["tempheatmap"] = normPlayer["data.playerHeatMaps"].apply(lambda x: [y['currentValue'] for y in x])
        player["tempmodifications"] =  normPlayer["data.modifications"].apply(lambda x: ";".join([y['modification']['name'] for y in x]))    
        player["temppositions"] = normPlayer["data.positions"].apply(lambda p: [(q["x"], q["y"]) for q in p])
        player["temppositions"]= player["temppositions"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
        player["temppositionname"] = normPlayer["data.positions"].apply(lambda p: [(q["positionName"]) for q in p])
        player["temppositionname"]= player["temppositionname"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
        player["batting_rating"] = normPlayer["data.attributes"].apply(raw_rating, args=("batting",))
        player["pitching_rating"] = normPlayer["data.attributes"].apply(raw_rating, args=("pitching",))
        player["defense_rating"] = normPlayer["data.attributes"].apply(raw_rating, args=("defense",))
        player["running_rating"] = normPlayer["data.attributes"].apply(raw_rating, args=("running",))
        player["vibes_rating"] = normPlayer["data.attributes"].apply(raw_rating, args=("vibes",))
        
        for attr in attribute_list:
            player[attr.lower()] = normPlayer["data.attributes"].apply(pick_attribute, args=(attr,))

        next_timestamp = max(player["valid_from"] )
            
        #Remove team_id/valid_from combos that have already been processed
        player = pd.merge(player, players_processed, indicator=True, how='left').query('_merge=="left_only"').drop('_merge', axis=1)
        
        #And *then* concat new events processed
        players_processed_new = player[["player_id","valid_from"]].copy()
        players_processed = pd.concat([players_processed, players_processed_new])   
        
        #As long as we didn't dedupe down to zero, we have records to write            
        if player.shape[0] != 0:    
            print('Writing players to Postgres')
            #player.to_sql('players',engine,schema='delta_data',if_exists='append',index=False)  
        
        
        print("{0}: processed player page {1}, {2} new records added.".format(datetime.now(), pages, player.shape[0]))
        
        #If we had less than the max records for this API (1000), we hit the last page
        #Maybe this should be an ELSE of the If above, for when it ends up as zero?  Wasted processing?
        if raw_players.shape[0] < 1000:
            conn.execute("UPDATE delta_data.api_loads_meta SET player_timestamp = '{0}'".format(next_timestamp))     
            break
         
        pages +=1 
    
    #TO DO: Kick off player_heatmaps (and others?), truncate or populate only new records    
        
        
def teamsMain():
    pages = 1

    next_timestamp = get_object_timestamp('team')
    
    #loop through results from API, starting at next_timestamp each time, until no more records 
    while True:
        
        raw_teams = pd.read_json("https://api2.sibr.dev/chronicler/v0/versions?kind=team&order=asc&after={0}".format(next_timestamp))     
        
        normTeams = pd.json_normalize(raw_teams["items"])
        
        #Lets go on a side adventure to do rosters!
        normRostersWide = pd.json_normalize(normTeams["data.roster"])
        normRosterSlots = {}  
        #loop through all roster columns to make a single column RosterSlot dataframe
        i = 0
        for x in normRostersWide.items():
            normRosterSlots[i] = pd.json_normalize(normRostersWide[i])
            normRosterSlots[i]["team_id"] = normTeams["entity_id"]
            normRosterSlots[i]["valid_from"] = normTeams["valid_from"]
            normRosterSlots[i]["valid_until"] = normTeams["valid_to"]
            
            #remove empties
            normRosterSlots[i] = normRosterSlots[i][normRosterSlots[i]["id"].notna()]
            
            normRosterSlots[i]["active"]= normRosterSlots[i]["rosterSlots"].apply(lambda x: [y['active'] for y in x])
            normRosterSlots[i]["active"]= normRosterSlots[i]["active"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
            normRosterSlots[i]["location"]= normRosterSlots[i]["rosterSlots"].apply(lambda x: [y['location'] for y in x])
            normRosterSlots[i]["location"]= normRosterSlots[i]["location"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
            normRosterSlots[i]["orderIndex"]= normRosterSlots[i]["rosterSlots"].apply(lambda x: [y['orderIndex'] for y in x])
            normRosterSlots[i]["orderIndex"]= normRosterSlots[i]["orderIndex"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
    
            if i ==0:
                normRosterAll = normRosterSlots[0].copy()
            else:
                normRosterAll = pd.concat([normRosterAll, normRosterSlots[i]])
    
            i +=1
        
        teamRoster = normRosterAll[["id", "team_id", "valid_from","valid_until", "active", "location", "orderIndex"]].copy()
        teamRoster.rename(columns={"id":"player_id","orderIndex":"order_index"}, inplace=True)
        
        teamRoster.to_sql('team_roster',engine,schema='delta_data',if_exists='append',index=False)
        
        #OK now back to actual team stuff    
        teams = normTeams[["entity_id", "valid_from", "valid_to", "data.activeTeam", "data.division.id", "data.locationName",
                           #"data.modifications", 
                           "data.name", "data.nickname","data.primaryColor","data.secondaryColor",
                           "data.shorthand","data.slogan"]].copy()
        teams.rename(columns={"entity_id":"team_id", "valid_to":"valid_until", "data.activeTeam":"active", "data.division.id":"tempdivision",
                              "data.locationName":"location", 
                              #"data.modifications":"tempmodifications", 
                              "data.name":"fullname","data.nickname":"nickname","data.primaryColor":"team_primarycolor",
                              "data.secondaryColor":"team_secondarycolor","data.shorthand":"team_shorthand","data.slogan":"team_slogan",
                              }, inplace=True)
        
        teams["losses"]= normTeams["data.standings"].apply(lambda x: [y['losses'] for y in x])
        teams["losses"]= teams["losses"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
        teams["wins"]= normTeams["data.standings"].apply(lambda x: [y['wins'] for y in x])
        teams["wins"]= teams["wins"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
        teams["season_id"]= normTeams["data.standings"].apply(lambda x: [y['seasonId'] for y in x])
        teams["season_id"]= teams["season_id"].apply(lambda x: x[0] if len(x) > 0 else np.nan)
        #teams['tempmodifications'] = json.dumps(teams['tempmodifications'])
     
     
        #First pass, create log of teams processed.  fter 1st pass, concat teams processed this loop
        if pages == 1:
            teams_processed = teams[["team_id","valid_from"]].copy()
        else:
            #Remove team_id/valid_from combos that have already been processed
            teams = pd.merge(teams, teams_processed, indicator=True, how='left').query('_merge=="left_only"').drop('_merge', axis=1)
            
            #And *then* concat new events processed
            teams_processed_new = teams[["team_id","valid_from"]].copy()
            teams_processed = pd.concat([teams_processed, teams_processed_new])   
     
        if teams.shape[0] != 0:    
            #teams.to_sql('teams',engine,schema='delta_data',if_exists='append',index=False)  
            
            next_timestamp = max(teams["valid_from"] )
    
            print("{0}: processed team page {1}, records {2}.".format(datetime.now(), pages, teams.shape[0]))
        
        if raw_teams.shape[0] < 1000:
            #conn.execute("UPDATE delta_data.api_loads_meta SET team_timestamp = '{0}'".format(next_timestamp))     
            break
     
        pages +=1  
    

def gamesMain():
    pages = 1

    #Unlike the others, for now I'm truncating and full replacing 
    #Because there aren't a ton of game objects, and it allows us the easily
    #Get the data for completed games that we didn't have before.
    #Also ... laziness.
    conn.execute("TRUNCATE TABLE delta_data.games;")
    
    raw_games = pd.read_json("https://api2.sibr.dev/chronicler/v0/entities?kind=game")
    
    while True:    
        
        if raw_games.shape[0] == 0:
            break
        
        norm_games = pd.json_normalize(raw_games["items"])
        games = norm_games[["entity_id","data.seasonId","data.day","data.cancelled","data.complete",
                            "data.awayPitcher.id","data.awayTeam.id","data.homePitcher.id","data.homeTeam.id",
                            "data.gameLoserId", "data.gameWinnerId",
                            "data.weather.name","data.numberInSeries","data.seriesLength"]] .copy()
        
        games.rename(columns={"entity_id":"game_id","data.seasonId":"season_id", "data.day":"gameday", "data.cancelled":"cancelled","data.complete":"completed",
                              "data.awayPitcher.id":"away_pitcher_id","data.awayTeam.id":"away_team_id",
                              "data.homePitcher.id":"home_pitcher_id","data.homeTeam.id":"home_team_id",
                              "data.gameLoserId":"losing_team_id","data.gameWinnerId":"winning_team_id",
                              "data.weather.name":"weather", "data.numberInSeries":"number_in_series","data.seriesLength":"series_length"}, inplace=True)
        interim_game_states = pd.json_normalize(norm_games["data.gameStates"])   
        norm_game_states = pd.json_normalize(interim_game_states[0])
        game_states_to_append = norm_game_states[["awayScore","homeScore","inning","ballsNeeded","strikesNeeded","outsNeeded",
                                                  "totalBases","shame"]].copy()
        game_states_to_append.rename(columns={"awayScore":"away_score","homeScore":"home_score","inning":"innings",
                                              "ballsNeeded":"balls_needed","strikesNeeded":"strikes_needed","outsNeeded":"outs_needed",
                                              "totalBases":"total_bases"}, inplace=True)
        games = games.join(game_states_to_append)
        #games.to_sql('games',engine,schema='delta_data',if_exists='append',index=False)
        
        next_page = raw_games["next_page"][0]
        print("{0}: processed games page {1}, records {2}.".format(datetime.now(), pages, games.shape[0]))
          
        #If we've gotten this far, get the next page of events
        raw_games = pd.read_json("https://api2.sibr.dev/chronicler/v0/entities?kind=game&page={}".format(next_page))
    

def gameEventsMain():
    pages = 1
    
    after = get_object_timestamp('game_events')
    
    raw_game_events = pd.read_json("https://api2.sibr.dev/chronicler/v0/game-events?order=asc&after={}".format(after))

    while True:

        norm_game_events = pd.json_normalize(raw_game_events["items"])
        game_events = norm_game_events[["game_id","timestamp"]].copy()
        game_events["bases_occupied"]= norm_game_events["data.changedState.baserunners"].apply(lambda x: [y['base'] for y in x] if type(x)==list else x)
        game_events["baserunner_ids"]= norm_game_events["data.changedState.baserunners"].apply(lambda x: [y['id'] for y in x] if type(x)==list else x)
        events_to_append = norm_game_events[["data.displayOrder","data.displayText"]].copy()
        for event in append_events:

            if event in norm_game_events:
                events_to_append[event] = norm_game_events[event]
            else:
                events_to_append[event] = np.nan

        game_events = game_events.join(events_to_append)
        game_events.rename(columns={"data.displayOrder":"display_order","data.displayText":"display_text","data.changedState.batter.id":"batter_id",
                                    "data.changedState.pitcher.id":"pitcher_id","data.changedState.balls":"balls",
                                    "data.changedState.strikes":"strikes","data.changedState.outs":"outs","data.changedState.awayScore":"away_score",
                                    "data.changedState.homeScore":"home_score","data.changedState.inning":"inning"}, inplace=True)

        #First pass, create log of events processed.  fter 1st pass, concat events processed this loop
        if pages == 1:
            events_processed = game_events[["game_id", "display_order","timestamp"]].copy()
        else:
            #Remove game_id/display_order combos that have already been processed
            game_events = pd.merge(game_events, events_processed, indicator=True, how='left').query('_merge=="left_only"').drop('_merge', axis=1)
            
            #And *then* concat new events processed
            events_processed_new = game_events[["game_id", "display_order", "timestamp"]].copy()
            events_processed = pd.concat([events_processed, events_processed_new])

        #game_events.to_sql('game_events_raw',engine,schema='ndata',if_exists='append',index=False)
        
        #Once we got less than the max 5000, that was the last time through the loop
        if raw_game_events.shape[0] < 5000:
            break
            #TO DO: update time in SQL for next load to start
            
        after = max(game_events["timestamp"])

        print("{0}: processed page {1}, up until timestamp {2}.".format(datetime.now(), pages, after))
        
        pages +=1   
        #If we've gotten this far, get the next page of events
        raw_game_events = pd.read_json("https://api2.sibr.dev/chronicler/v0/game-events?after={}".format(after))   
    
if __name__ == "__main__":
    # execute only if run as a script
    playersMain()
    teamsMain()
    gamesMain()
    gameEventsMain()